"""
校园食堂AI营养分析 - 后端服务
拍照菜单/菜品标签 → OCR识别菜名 → 返回营养成分
"""
import io
import json
import os
import re
from difflib import get_close_matches
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image

app = FastAPI(title="食堂AI营养分析")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 加载营养数据库
DB_PATH = Path(__file__).parent / "nutrition.json"
with open(DB_PATH, encoding="utf-8") as f:
    NUTRITION_DB = json.load(f)

# ========== OCR 引擎（优先级：百度免费OCR > EasyOCR 离线 > Mock） ==========
_ocr = None
_ocr_type = None  # "baidu" / "easyocr" / None
_ocr_init_done = False

# 去 https://console.bce.baidu.com 创建"文字识别"应用，获取免费 API Key
BAIDU_OCR_KEY = os.environ.get("BAIDU_OCR_KEY", "")
BAIDU_OCR_SECRET = os.environ.get("BAIDU_OCR_SECRET", "")
MOCK_MODE = os.environ.get("MOCK_MODE", "1") == "1"


def get_ocr():
    """
    懒加载 OCR 引擎。
    优先百度免费 OCR（每天5万次，无需下载模型），
    备选 EasyOCR（离线免费，需下载模型）。
    """
    global _ocr, _ocr_type, _ocr_init_done
    if _ocr_init_done:
        return _ocr

    _ocr_init_done = True

    # 1. 百度免费 OCR —— 最优选择
    if BAIDU_OCR_KEY and BAIDU_OCR_SECRET:
        try:
            # 测试获取 token
            import requests as _r
            resp = _r.post(
                "https://aip.baidubce.com/oauth/2.0/token",
                params={
                    "grant_type": "client_credentials",
                    "client_id": BAIDU_OCR_KEY,
                    "client_secret": BAIDU_OCR_SECRET,
                },
                timeout=10,
            )
            if "access_token" in resp.json():
                _ocr_type = "baidu"
                _ocr = True  # 标记可用，实际调用不缓存 token
                print("[OCR] 百度免费 OCR 就绪（每天50000次免费）")
                return _ocr
            else:
                print(f"[OCR] 百度 Key 验证失败: {resp.json()}")
        except Exception as e:
            print(f"[OCR] 百度 OCR 连接失败: {e}")

    # 2. EasyOCR 离线 —— 备选
    try:
        import easyocr
        _ocr = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
        _ocr_type = "easyocr"
        print("[OCR] EasyOCR 初始化成功（离线/免费）")
        return _ocr
    except ImportError:
        print("[OCR] EasyOCR 未安装: pip install easyocr")
    except Exception as e:
        print(f"[OCR] EasyOCR 初始化失败: {e}")

    _ocr = False
    if not BAIDU_OCR_KEY:
        print("[OCR] 未配置百度 OCR Key，使用 Mock 模式")
        print("  免费申请: https://console.bce.baidu.com → 文字识别 → 创建应用")
    return None


def ocr_extract_text(image_bytes: bytes) -> str:
    """用 OCR 从图片中提取所有文字"""
    ocr = get_ocr()
    if ocr is None:
        return ""

    # 百度 OCR: HTTP API 调用
    if _ocr_type == "baidu":
        import base64
        import requests as _r
        # 获取 token（每次调用重新获取，token 有效期30天）
        resp = _r.post(
            "https://aip.baidubce.com/oauth/2.0/token",
            params={
                "grant_type": "client_credentials",
                "client_id": BAIDU_OCR_KEY,
                "client_secret": BAIDU_OCR_SECRET,
            },
            timeout=10,
        )
        token = resp.json()["access_token"]
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        resp = _r.post(
            f"https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic?access_token={token}",
            data={"image": img_b64},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        data = resp.json()
        if "words_result" in data:
            texts = [w["words"] for w in data["words_result"]]
            return " ".join(texts)
        print(f"[OCR] 百度返回异常: {data}")
        return ""

    # EasyOCR: 本地模型推理
    if _ocr_type == "easyocr":
        img = Image.open(io.BytesIO(image_bytes))
        import numpy as np
        img_array = np.array(img)
        results = ocr.readtext(img_array)
        texts = [r[1] for r in results if r[1]]
        return " ".join(texts)

    return ""


def extract_dish_names(raw_text: str) -> list[str]:
    """
    从 OCR 提取的菜单文本中匹配菜品名。
    策略: 预处理 → 按价格分割 → 优先长名称精确匹配 → 子串 → 模糊。
    """
    # 0. 预处理
    #    a. 合并中文间的空格: "牛 肉" → "牛肉"
    text = re.sub(r'(?<=[一-鿿])\s+(?=[一-鿿])', '', raw_text)
    #    b. 去掉分类标题: "汤粉面系列", "火锅类" 等
    text = re.sub(r'\S+(?:系列|类)\s*', ' ', text)
    #    c. 去掉水印和噪音
    text = re.sub(r'X?www\.[^\s]+', ' ', text)
    text = re.sub(r'ID[,\d]+N\d+[\d:]+', ' ', text)
    text = re.sub(r'(?:nipic|pic)\.[^\s]*', ' ', text, flags=re.IGNORECASE)
    #    d. 统一价格符号: ￥3.0 → 3元, ¥3 → 3元
    text = re.sub(r'[￥¥](\d+\.?\d*)', r'\1元', text)
    #    e. 去掉"时价"
    text = text.replace('时 价', ' ').replace('时价', ' ')

    # 1. 按价格标记分割
    price_splits = re.split(r'\d+\.?\d*\s*元\s*(?:/份|/例|份|/碗|/盘)?', text)

    all_segments = []
    for part in price_splits:
        part = part.strip()
        part = re.sub(r'^[>\s/\d]+', '', part)
        if len(part) < 2:
            continue

        # 对每个价格段，尝试各种切分方式提取菜名候选
        for s in re.split(r'[\s,，。、\-\+\|\\（）\(\)]', part):
            s = s.strip()
            if s and re.search(r'[一-鿿]', s) and 2 <= len(s) <= 15:
                if not re.match(r'^[\d\.\s\-=/]+$', s):
                    all_segments.append(s)
                    # 处理 "辣椒炒肉粉/面" → 生成 "辣椒炒肉粉"、"辣椒炒肉面"
                    if '/' in s and re.search(r'[一-鿿]/[一-鿿]', s):
                        # 找到 "/" 前后的两个中文字
                        m = re.search(r'([一-鿿])/([一-鿿])', s)
                        if m:
                            a, b = m.group(1), m.group(2)
                            # "xxxA/B" → "xxxA" 和 "xxxB"
                            v1 = s[:m.start()] + a + s[m.end():]
                            v2 = s[:m.start()] + b + s[m.end():]
                            if v1 != s and re.search(r'[一-鿿]', v1): all_segments.append(v1)
                            if v2 != s and re.search(r'[一-鿿]', v2): all_segments.append(v2)

    # 去重保序
    seen_segs = set()
    unique_segs = []
    for s in all_segments:
        if s not in seen_segs:
            seen_segs.add(s)
            unique_segs.append(s)

    # 2. 匹配——按 DB 菜名长度降序（长的优先：辣椒炒肉面 > 辣椒炒肉 > 炒肉）
    db_sorted = sorted(NUTRITION_DB.keys(), key=len, reverse=True)
    found = []
    seen = set()

    for seg in unique_segs:
        if seg in seen:
            continue
        # a. 精确匹配
        if seg in NUTRITION_DB:
            seen.add(seg); found.append(seg); continue
        # b. DB 值是 seg 的子串（长优先）
        for dish in db_sorted:
            if dish in seg and dish not in seen:
                seen.add(dish); found.append(dish); break

    # 3. 模糊匹配补充
    for seg in unique_segs:
        if seg in seen:
            continue
        # 双向子串: seg 是 DB 菜名的子串
        for dish in db_sorted:
            if seg in dish and dish not in seen:
                seen.add(dish); found.append(dish); break
        # 文字相似度
        if seg not in seen:
            matches = get_close_matches(seg, NUTRITION_DB.keys(), n=1, cutoff=0.5)
            for m in matches:
                if m not in seen:
                    seen.add(m); found.append(m)

    # 4. 全局兜底
    if len(found) < 2:
        for dish in db_sorted:
            if dish not in seen and dish in text:
                seen.add(dish); found.append(dish)
                if len(found) >= 6:
                    break

    # 5. 去重：合并同类型菜名（辣椒炒肉面 / 辣椒炒肉粉 → 只保留一个）
    found.sort(key=len, reverse=True)
    filtered = []
    for dish in found:
        covered = False
        for kept in filtered:
            # 完全包含
            if dish in kept:
                covered = True; break
            # 只差最后一个字（粉/面 互换）
            if len(dish) >= 3 and len(kept) >= 3:
                if dish[:-1] == kept[:-1] and dish[-1] != kept[-1]:
                    covered = True; break
        if not covered:
            filtered.append(dish)

    return filtered[:12]


def mock_recognize() -> tuple[list[str], str]:
    """
    模拟识别——返回"一荤一素一主食"的合理搭配。
    同时返回模拟的 OCR 文本，让结果页有完整的展示效果。
    """
    combos = [
        (["红烧肉", "蒜蓉西兰花", "蛋炒饭"], "红烧肉 15元\n蒜蓉西兰花 6元\n蛋炒饭 8元"),
        (["宫保鸡丁", "醋溜白菜", "牛肉面"], "宫保鸡丁 12元\n醋溜白菜 5元\n牛肉面 14元"),
        (["糖醋里脊", "西红柿炒鸡蛋", "米饭"], "糖醋里脊 14元\n西红柿炒鸡蛋 8元"),
        (["回锅肉", "麻婆豆腐", "蛋炒饭"], "回锅肉 13元\n麻婆豆腐 7元\n蛋炒饭 8元"),
        (["黄焖鸡", "干煸豆角", "饺子"], "黄焖鸡 15元\n干煸豆角 8元\n饺子 12元"),
    ]
    import random
    dishes, raw_text = random.choice(combos)
    return dishes, raw_text


def match_nutrition(dish_name: str) -> dict | None:
    """通过菜品名匹配营养数据，支持模糊匹配"""
    if dish_name in NUTRITION_DB:
        return NUTRITION_DB[dish_name]
    matches = get_close_matches(dish_name, NUTRITION_DB.keys(), n=1, cutoff=0.5)
    if matches:
        return NUTRITION_DB[matches[0]]
    return None


def analyze_image(image_bytes: bytes) -> dict:
    """
    完整分析流程：
    1. 图片 → OCR 提取文字
    2. 文字中匹配菜品名
    3. 查询营养数据库
    4. 汇总结果
    """
    if MOCK_MODE:
        dishes, raw_text = mock_recognize()
    else:
        raw_text = ocr_extract_text(image_bytes)
        dishes = extract_dish_names(raw_text) if raw_text else []

    items = []
    for name in dishes:
        nutrition = match_nutrition(name)
        if nutrition:
            items.append({"name": name, **nutrition})
        else:
            items.append({
                "name": name,
                "calories": 0, "protein": 0, "fat": 0, "carbs": 0,
                "unit": "未知",
            })

    total = {
        "calories": sum(i["calories"] for i in items),
        "protein": sum(i["protein"] for i in items),
        "fat": sum(i["fat"] for i in items),
        "carbs": sum(i["carbs"] for i in items),
    }

    if total["calories"] < 400:
        level, tip = "light", "这餐热量偏低，可以适当加个菜"
    elif total["calories"] < 700:
        level, tip = "balanced", "营养均衡，继续保持"
    elif total["calories"] < 1000:
        level, tip = "moderate", "热量适中，注意搭配蔬菜"
    else:
        level, tip = "heavy", "这餐热量偏高，建议少油少盐"

    return {
        "items": items,
        "total": total,
        "advice": {"level": level, "tip": tip},
        "raw_text": raw_text,
    }


# ===== API 路由 =====

@app.post("/api/analyze")
async def api_analyze(file: UploadFile = File(...)):
    """上传菜单/菜品标签图片，返回营养成分分析"""
    image_bytes = await file.read()
    result = analyze_image(image_bytes)
    return {"code": 0, "data": result}


@app.get("/api/search")
async def api_search(name: str):
    """手动搜索菜品营养数据"""
    nutrition = match_nutrition(name)
    if nutrition:
        item = {"name": name, **nutrition}
    else:
        item = {
            "name": name,
            "calories": 0, "protein": 0, "fat": 0, "carbs": 0,
            "unit": "未知",
        }
    # 复用统一返回格式
    total = {
        "calories": item["calories"],
        "protein": item["protein"],
        "fat": item["fat"],
        "carbs": item["carbs"],
    }
    level = "balanced" if total["calories"] > 0 else "info"
    return {
        "code": 0,
        "data": {
            "items": [item],
            "total": total,
            "advice": {"level": level, "tip": "手动查询结果" if total["calories"] > 0 else "未找到该菜品，请检查菜名"},
            "raw_text": f"搜索: {name}",
        },
    }


@app.get("/api/dishes")
async def api_dishes():
    """获取所有支持的菜品列表（供前端搜索）"""
    return {"code": 0, "data": list(NUTRITION_DB.keys())}


@app.get("/api/health")
async def api_health():
    return {"status": "ok", "mock_mode": MOCK_MODE}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    if BAIDU_OCR_KEY:
        mode = "百度免费OCR"
    elif not MOCK_MODE:
        mode = "EasyOCR离线"
    else:
        mode = "Mock模拟"
    print(f"\n  启动模式: {mode}")
    print(f"  API地址: http://0.0.0.0:{port}")
    print(f"  文档:    http://0.0.0.0:{port}/docs\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
