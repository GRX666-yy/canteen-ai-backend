"""快速测试后端API"""
import sys
import requests

BASE = "http://localhost:8000"

# 1. 健康检查
r = requests.get(f"{BASE}/api/health")
print("健康检查:", r.json())

# 2. 获取菜品列表
r = requests.get(f"{BASE}/api/dishes")
print(f"支持 {len(r.json()['data'])} 道菜品")

# 3. 测试分析
if len(sys.argv) > 1:
    with open(sys.argv[1], "rb") as f:
        r = requests.post(f"{BASE}/api/analyze", files={"file": f})
    data = r.json()
    if data["code"] == 0:
        d = data["data"]
        print(f"\nOCR 原始文本: {d.get('raw_text', '')}")
        if d["items"]:
            print("\n=== 识别结果 ===")
            for item in d["items"]:
                print(f"  {item['name']}: {item['calories']}kcal  蛋白{item['protein']}g  脂肪{item['fat']}g  碳水{item['carbs']}g")
            print(f"\n总计: {d['total']['calories']}kcal")
            print(f"建议: [{d['advice']['level']}] {d['advice']['tip']}")
        else:
            print("\n未识别到菜品，请确保图片中有清晰的菜单文字")
    else:
        print("错误:", data)
else:
    print("\n用法: python test_api.py <图片路径>")
    print("支持: 手机拍食堂菜单/菜品标签/价目表的照片")
