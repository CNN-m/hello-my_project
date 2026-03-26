from flask import Flask, render_template, request, redirect
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
import requests
import base64
import re

app = Flask(__name__)

# --- 数据库配置 ---
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'account_book.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


class Record(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(50))
    type = db.Column(db.String(10))
    amount = db.Column(db.Float)
    category = db.Column(db.String(50))
    note = db.Column(db.String(100))


def get_stats(records, year_records):
    total_income = sum(r.amount for r in records if r.type == '收入')
    total_expense = sum(r.amount for r in records if r.type == '支出')
    year_income = sum(r.amount for r in year_records if r.type == '收入')
    year_expense = sum(r.amount for r in year_records if r.type == '支出')
    return {
        "total_income": round(total_income, 2), "total_expense": round(total_expense, 2),
        "balance": round(total_income - total_expense, 2),
        "year_income": round(year_income, 2), "year_expense": round(year_expense, 2),
        "year_balance": round(year_income - year_expense, 2)
    }


# --- 百度AI连接函数
def get_access_token():
    try:
        api_key = "owkh4sqF8bnIX3iR9wwZL28w"
        secret_key = "MfGPTwdCFqGFY67G2b0nwGUTq4Dwwf7u"

        url = f"https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials&client_id={api_key}&client_secret={secret_key}"
        response = requests.post(url, timeout=3)
        result = response.json()

        if "access_token" in result:
            return result["access_token"]
        return None
    except:
        return None


@app.route('/')
def home():
    selected_month = request.args.get('month')
    if not selected_month: selected_month = datetime.now().strftime('%Y-%m')
    selected_year = selected_month.split('-')[0]

    records = Record.query.filter(Record.date.startswith(selected_month)).order_by(Record.date.desc()).all()
    year_records = Record.query.filter(Record.date.startswith(selected_year)).all()
    stats = get_stats(records, year_records)

    return render_template('index.html', records=records, current_month=selected_month, current_year=selected_year,
                           **stats,
                           default_date=datetime.now().strftime('%Y-%m-%d'),
                           default_amount="", default_category="", default_note="")


# --- 升级版识别逻辑 ---
@app.route('/upload', methods=['POST'])
def upload_image():
    if 'screenshot' not in request.files: return redirect('/')
    file = request.files['screenshot']
    if file.filename == '': return redirect('/')

    print(f"正在处理图片...")

    ai_amount = ""
    ai_category = ""
    ai_note = "识别失败"

    token = get_access_token()

    if token:
        try:
            img_data = file.read()
            b64_img = base64.b64encode(img_data).decode()

            ocr_url = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"
            headers = {'content-type': 'application/x-www-form-urlencoded'}
            params = {"image": b64_img}
            response = requests.post(ocr_url + "?access_token=" + token, data=params, headers=headers)

            json_result = response.json()
            words_result = json_result.get("words_result", [])

            # ---  智能分析核心逻辑 (升级版)  ---

            found_amount = False

            # 策略 1：先遍历每一行，寻找“明确的线索”
            for item in words_result:
                line_text = item['words']

                # 如果这行字里包含“余额”，直接跳过，防止识别成余额
                if "余额" in line_text:
                    continue

                # 如果这行字里包含 "-" (负号)，大概率是支出
                # 比如 "-14.80"
                if "-" in line_text:
                    # 尝试提取数字
                    nums = re.findall(r"\d+\.\d{2}", line_text)
                    if nums:
                        ai_amount = nums[0]
                        found_amount = True
                        break  # 找到了就停止，不再往下找

            # 策略 2：如果没找到负号，那就找最大的数字，但是要排除太大的(可能是单号)
            if not found_amount:
                all_text = " ".join([w['words'] for w in words_result])
                amounts = re.findall(r"\d+\.\d{2}", all_text)

                valid_amounts = []
                for a in amounts:
                    try:
                        val = float(a)
                        # 排除掉年份(2025)和过大的数字(超过10万通常不是日常消费)
                        if val != 2025.00 and val < 100000:
                            valid_amounts.append(val)
                    except:
                        pass

                if valid_amounts:
                    # 取最大的那个
                    ai_amount = max(valid_amounts)

            # -----------------------------------------------

            # 提取分类和备注 (逻辑不变)
            all_text_str = " ".join([w['words'] for w in words_result])

            if any(k in all_text_str for k in ["餐饮", "美食", "饭", "饿了么", "美团", "麦当劳"]):
                ai_category = "餐饮"
            elif any(k in all_text_str for k in ["出行", "打车", "滴滴", "车", "油"]):
                ai_category = "交通"
            elif any(k in all_text_str for k in ["超市", "便利", "拼多多", "淘宝", "京东", "买菜"]):
                ai_category = "购物"
            elif "充值" in all_text_str:
                ai_category = "生活缴费"

            ai_note = all_text_str[:20] + "..."

        except Exception as e:
            print("❌ 识别过程崩了：", e)

    # 重新加载页面
    current_month = datetime.now().strftime('%Y-%m')
    current_year = current_month.split('-')[0]
    records = Record.query.filter(Record.date.startswith(current_month)).order_by(Record.date.desc()).all()
    year_records = Record.query.filter(Record.date.startswith(current_year)).all()
    stats = get_stats(records, year_records)

    return render_template('index.html', records=records, current_month=current_month, current_year=current_year,
                           **stats,
                           default_date=datetime.now().strftime('%Y-%m-%d'),
                           default_amount=ai_amount,
                           default_category=ai_category,
                           default_note=ai_note)


@app.route('/add', methods=['POST'])
def add_record():
    try:
        amount = float(request.form.get('amount'))
    except:
        return "金额格式错误"
    new_record = Record(date=request.form.get('date'), type=request.form.get('type'), amount=amount,
                        category=request.form.get('category'), note=request.form.get('note'))
    db.session.add(new_record)
    db.session.commit()
    return redirect('/')


@app.route('/delete/<int:id>')
def delete(id):
    record = Record.query.get(id)
    if record: db.session.delete(record); db.session.commit()
    return redirect('/')


if __name__ == '__main__':
    with app.app_context(): db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)