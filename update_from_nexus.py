# Puyo Nexus Wiki から、シートにない新しいカードを取り込むプログラム
#
# 使い方（ターミナルでこのフォルダに移動してから）:
#   python3 update_from_nexus.py   ← Wikiから不足カードを取得して nexus_cards.json に保存
#   python3 make_carddb.py         ← シート＋Wikiのデータを合体して carddb.js を生成
#
# ・シートにあるカードはシートのデータが優先されます
# ・Wiki由来のカードは攻撃力が「とっくんなし」のLv.MAX値になります
# ・取得済みのカードは nexus_cards.json に記録され、次回はスキップされます（差分だけ取得）

import csv
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request

from make_carddb import GID_SKILL, SHEET_ID, download_csv

API = "https://puyonexus.com/mediawiki/api.php"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nexus_cards.json")
UA = {"User-Agent": "puyoquest-damage-calc updater (personal tool)"}

COLOR_EN = {"Red": 1, "Blue": 2, "Green": 3, "Yellow": 4, "Purple": 5}
TYPE_EN = {"Attack": "こうげき", "Balance": "バランス", "Balanced": "バランス",
           "HP": "たいりょく", "Recover": "かいふく", "Recovery": "かいふく"}
AILMENTS = {"怒り": 2, "怯え": 2, "脱力": 2.5, "麻痺": 3}

# 特訓パターンごとのステータス加算 (体力+, 攻撃+, 回復+)
# ユーザーの検索シート「特訓パターン一覧」より。
# Wikiのカテゴリから確実に判別できるフルパワー(F)とフェス(E)のみ使用
TRAIN_BONUS = {
    ("7", "F"): (3500, 1750, 700),
    ("7", "E"): (3400, 1700, 680),
    ("6", "F"): (2600, 1300, 520),
    ("6", "E"): (2600, 1300, 520),
}


def api_get(params):
    params = dict(params, format="json")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req) as res:
        return json.load(res)


def known_codes_from_sheet():
    """シートの画像URLから、すでに持っている(シリーズ番号, レア度)の一覧を作る"""
    rows = download_csv(GID_SKILL)[1:]
    known = set()
    for r in rows:
        m = re.search(r"Img(\d{5,7})", r[8] if len(r) > 8 else "")
        if m:
            num = int(m.group(1))
            series, suffix = num // 100, num % 100
            known.add((series, suffix % 10))  # 17→★7(とくもり絵), 07→★7
    return known


def list_numeric_templates():
    """Wikiのカードデータ置き場（数字だけの名前のテンプレート）を全部列挙する"""
    codes = []
    cont = {}
    while True:
        d = api_get({"action": "query", "list": "allpages", "apnamespace": 10,
                     "aplimit": 500, **cont})
        for p in d["query"]["allpages"]:
            name = p["title"].split(":", 1)[1]
            if re.fullmatch(r"\d{5,7}", name):
                codes.append(int(name))
        if "continue" not in d:
            break
        cont = {"apcontinue": d["continue"]["apcontinue"]}
        time.sleep(0.3)
    return codes


def parse_template(text):
    """カードテンプレートの中身を{項目名: 値}に分解する"""
    fields = {}
    # 「|hpmax=6227|atkmax=5338|...」のように1行に複数書かれる単純な項目はピンポイントで拾う
    for key in ("code", "rarity", "name", "jpname", "color", "color2", "type1",
                "maxlv", "cost", "hpmax", "atkmax", "rcvmax",
                "tse10", "ts2e10", "ts3e10", "kse10"):
        m = re.search(r"\|\s*" + key + r"\s*=\s*([^|\n{}]*)", text)
        if m:
            fields[key] = m.group(1).strip()
    # 効果文（{{○|○}}などの記法を含むので行ごと拾う）
    for line in text.splitlines():
        m = re.match(r"\|\s*(jpase|jpasfe|jplse|jptse|jpts2e|jpts3e|jpkse|jpgae)\s*=\s*(.*)", line.strip())
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def strip_wiki(s):
    """効果文からwiki記法を取り除いて読みやすくする"""
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    return s.strip()


def parse_skills(jpase, jplse):
    """日本語の効果文から、ダメージ倍率を簡易判定する（Wiki自動判定）"""
    sk = []
    ail = []
    lm = None
    lr = ""

    def color_range(text):
        cols = [c for c in "赤青緑黄紫" if f"{c}属性カード" in text]
        if cols:
            return "".join(cols)
        return "味方全体"

    if jpase:
        # 「n倍」と「n％アップ」(=1+n/100倍)の両方の表記に対応
        vals = []
        for num, unit in re.findall(r"(?:攻撃力|攻撃値)を([\d.]+)(倍|％アップ|%アップ)", jpase):
            v = float(num)
            vals.append(v if unit == "倍" else round(1 + v / 100, 3))
        if vals and min(vals) > 1:
            sk.append({"v": min(vals), "r": color_range(jpase), "k": "攻撃エンハ", "t": jpase})
        m = re.search(r"受けるダメージを([\d.]+)倍", jpase)
        if m and float(m.group(1)) > 1:
            sk.append({"v": float(m.group(1)), "r": "味方全体", "k": "被ダメアップ", "t": jpase})
        if "解除" not in jpase:
            for name, v in AILMENTS.items():
                if name in jpase:
                    ail.append({"k": name, "v": v})

    if jplse:
        m = re.search(r"攻撃力を([\d.]+)倍", jplse)
        if m:
            atk = float(m.group(1))
            m2 = re.search(r"さらに([\d.]+)倍", jplse)
            if m2:
                atk = round(atk * float(m2.group(1)), 3)
            lm = [0, atk, 0]
            lr = color_range(jplse)

    return sk, ail, lm, lr


def parse_tokumori(f):
    """とくもりスキル(ESS)ときらめきオーラ(GA)を倍率行に変換する"""
    sk = []

    def color_range(text):
        cols = [c for c in "赤青緑黄紫" if f"{c}属性カード" in text]
        return "".join(cols) if cols else "味方全体"

    # とくもりスキル（第1〜第3、キラー）: Lv10の値を採用
    for txt_key, val_key in (("jptse", "tse10"), ("jpts2e", "ts2e10"),
                             ("jpts3e", "ts3e10"), ("jpkse", "kse10")):
        raw = f.get(txt_key, "")
        val = f.get(val_key, "")
        if not raw or not val:
            continue
        try:
            v = float(val)
        except ValueError:
            continue
        txt = strip_wiki(raw.replace("{{tsparam}}", val))
        if v <= 1:
            continue
        if "与えるダメージ" in txt:
            sk.append({"v": v, "r": "自身のみ", "k": "与ダメアップ(特盛)", "t": txt})
        elif "攻撃力" in txt or "攻撃値" in txt:
            # 「％アップ」表記は倍率に換算（150%アップ → 2.5倍）
            pct = "％アップ" in txt or "%アップ" in txt
            if pct and "Lv" in txt:
                v = round(1 + v * 10 / 100, 3)   # 「Lv.×n%」はLv.10換算
            elif pct:
                v = round(1 + v / 100, 3)
            if "ブーストエリア" in txt:
                k = "ブーストエリア強化(特盛)"
            elif "体力MAX" in txt:
                k = "攻撃エンハ(特盛体力MAX)"
            else:
                k = "攻撃エンハ(特盛)"
            sk.append({"v": v, "r": color_range(txt), "k": k, "t": txt})
        elif "プリズム" in txt and "効果" in txt:
            sk.append({"v": v, "r": "味方全体", "k": "プリズム効果アップ(特盛)", "t": txt})
        elif "クリティカル" in txt and "倍率" in txt:
            if "Lv" in txt and "％アップ" in txt.replace("%", "％"):
                cv = round(1 + (v * 10) / 100, 3)   # 「Lv.×n%」はLv.10換算: 4 → +40% → 1.4倍
            elif v >= 5:
                cv = round(1 + v / 100, 3)          # 「40%アップ」→ 1.4倍
            else:
                cv = v                               # すでに倍率表記
            sk.append({"v": cv, "r": "味方全体", "k": "クリティカル倍率up(特盛)", "t": txt})
        # 回復・体力系のとくもりはダメージに関係ないので入れない

    # きらめきオーラ: デッキ構成で値が変わるものが多いので、
    # 倍率が読み取れなければ空欄の行を作る（?ボタンで効果文を確認して手入力）
    raw = f.get("jpgae", "")
    if raw:
        txt = strip_wiki(raw)
        if "攻撃" in txt and ("倍" in txt or "アップ" in txt):
            m = re.search(r"攻撃(?:力|値)を([\d.]+)倍", txt)
            v = float(m.group(1)) if m else ""
            sk.append({"v": v, "r": color_range(txt), "k": "きらめきオーラ", "t": txt})

    return sk


def main():
    cache = []
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    cached_codes = {c["code"] for c in cache}

    print("シートの収録カードを確認中...")
    known = known_codes_from_sheet()
    print(f"  シートに画像がある(シリーズ, レア度): {len(known)}組")

    print("Wikiのカード一覧を取得中...")
    codes = list_numeric_templates()
    print(f"  Wikiのカードデータ: {len(codes)}件")

    # 足りないカード（★6/★7のみ）を洗い出す
    missing = []
    for code in codes:
        series, rarity = code // 100, code % 100
        if rarity in (6, 7) and (series, rarity) not in known and code not in cached_codes:
            missing.append(code)
    print(f"  シートにない★6/★7: {len(missing)}件 → Wikiから取得します")

    # 50件ずつまとめて取得
    new_cards = []
    for i in range(0, len(missing), 50):
        batch = missing[i:i + 50]
        titles = "|".join(f"Template:{c}" for c in batch)
        d = api_get({"action": "query", "prop": "revisions", "rvprop": "content",
                     "rvslots": "main", "titles": titles})
        img_titles = "|".join(f"File:Img{c}.png" for c in batch)
        di = api_get({"action": "query", "titles": img_titles, "prop": "imageinfo", "iiprop": "url"})
        img_urls = {}
        for p in di["query"]["pages"].values():
            m = re.search(r"Img(\d+)\.png", p["title"])
            if m and "imageinfo" in p:
                img_urls[int(m.group(1))] = p["imageinfo"][0]["url"]

        for p in d["query"]["pages"].values():
            if "revisions" not in p:
                continue
            f = parse_template(p["revisions"][0]["slots"]["main"]["*"])
            if "jpname" not in f or not f.get("jpname"):
                continue
            code = int(re.search(r"(\d+)", p["title"]).group(1))
            jpase = strip_wiki(f.get("jpase", ""))
            jplse = strip_wiki(f.get("jplse", ""))
            sk, ail, lm, lr = parse_skills(jpase, jplse)
            sk.extend(parse_tokumori(f))  # とくもりスキル・きらめきオーラ
            card = {
                "code": code,
                "n": f["jpname"],
                "r": str(code % 100),
                "t": TYPE_EN.get(f.get("type1", ""), f.get("type1", "")),
                "m": COLOR_EN.get(f.get("color", ""), 0),
                "s": COLOR_EN.get(f.get("color2", ""), 0),
                "u": img_urls.get(code, ""),
                "w": 1,  # Wiki由来の印
            }
            for key, field in (("a", "atkmax"), ("h", "hpmax"), ("c", "rcvmax")):
                try:
                    card[key] = int(f.get(field, ""))
                except ValueError:
                    pass
            if "a" in card:
                card["x"] = 1  # とっくんなしのLv.MAX値
            if f.get("name"):
                card["_page"] = f"PPQ:{f['name']}/★{card['r']}"  # カテゴリ確認用（後で消す）
            if jpase:
                card["ns"] = jpase
            if jplse:
                card["ls"] = jplse
            # カード詳細表示用: フルパワー・とくもり・きらめきオーラの効果文
            fp = strip_wiki(f.get("jpasfe", ""))
            if fp:
                card["fp"] = fp
            ts_texts = []
            for tk, vk in (("jptse", "tse10"), ("jpts2e", "ts2e10"),
                           ("jpts3e", "ts3e10"), ("jpkse", "kse10")):
                raw = f.get(tk, "")
                if raw:
                    ts_texts.append(strip_wiki(raw.replace("{{tsparam}}", f.get(vk, "◯"))))
            if ts_texts:
                card["ts"] = " ".join(f"{'①②③④'[i2]}{t}" for i2, t in enumerate(ts_texts))
            ga = strip_wiki(f.get("jpgae", ""))
            if ga:
                card["ga"] = ga
            if sk:
                card["sk"] = sk
            if ail:
                card["ail"] = ail
            if lm:
                card["lm"] = lm
                card["lr"] = lr
            new_cards.append(card)

        done = min(i + 50, len(missing))
        print(f"  取得中... {done}/{len(missing)}")
        time.sleep(0.5)

    # フルパワー/フェスをWikiのカテゴリから判別して、とっくん込みステータスに変換
    print("特訓パターンを判別中...")
    pages = [c for c in new_cards if c.get("_page") and "a" in c]
    trained = 0
    for i in range(0, len(pages), 25):
        batch = pages[i:i + 25]
        d = api_get({"action": "query", "prop": "categories", "cllimit": "max",
                     "titles": "|".join(c["_page"] for c in batch)})
        cats_by_title = {}
        for p in d["query"]["pages"].values():
            cats_by_title[p["title"]] = {c["title"] for c in p.get("categories", [])}
        for c in batch:
            cats = cats_by_title.get(c["_page"], set())
            pat = None
            if "Category:PPQ:Cards with Full Power Skill" in cats:
                pat = "F"
            elif "Category:PPQ:Puyo Fest Keyword" in cats:
                pat = "E"
            bonus = TRAIN_BONUS.get((c["r"], pat))
            if bonus:
                c["h"] = c.get("h", 0) + bonus[0]
                c["a"] = c.get("a", 0) + bonus[1]
                c["c"] = c.get("c", 0) + bonus[2]
                c.pop("x", None)  # とっくん込みになったので「とっくんなし」印を外す
                trained += 1
        time.sleep(0.4)
    for c in new_cards:
        c.pop("_page", None)
    print(f"  とっくん込みに変換: {trained}枚（フルパワー/フェス）")

    cache.extend(new_cards)
    with open(CACHE, "w", encoding="utf-8") as fp:
        json.dump(cache, fp, ensure_ascii=False, indent=1)
    print(f"完了! 新たに{len(new_cards)}枚を取得（合計{len(cache)}枚を nexus_cards.json に保存）")
    print("次に python3 make_carddb.py を実行すると carddb.js に反映されます")


if __name__ == "__main__":
    main()
