# carddb.js を作り直すプログラム
#
# スプレッドシートの「スキルDB」と「ステDB」をダウンロードして、
# アプリが読み込む carddb.js を生成します。
#
# 使い方（ターミナルでこのフォルダに移動してから）:
#   python3 make_carddb.py
#
# ※スプレッドシートが「リンクを知っている全員が閲覧可」になっている必要があります

import csv
import io
import json
import os
import unicodedata
import urllib.request

SHEET_ID = "19Na0yv6N2lf-StIXK2Aut0skU1qKw0cYI62xnt1jL3E"
GID_SKILL = "496870232"  # スキルDB タブ
GID_STAT = "866290965"   # ステDB タブ

COLOR = {1: "赤", 2: "青", 3: "緑", 4: "黄", 5: "紫"}


def norm(name):
    # 全角/半角・カッコの種類・スペースの違いを吸収してカード名を照合するための正規化
    return unicodedata.normalize("NFKC", name).replace(" ", "").replace("　", "")


# 敵に付与するとダメージが増える状態異常とその倍率（game8調べ・高い方のみ適用）
AILMENT_MAP = {"怒り": 2, "怯え": 2, "脱力": 2.5, "麻痺": 3}

# フィールド効果の攻撃倍率（game8調べ）: (倍率, 対象範囲)
FIELD_MAP = {
    "晴れ": (1.5, "同色"), "雨": (1.5, "同色"), "風": (1.5, "同色"),
    "雷": (1.5, "同色"), "雪": (1.5, "同色"),
    "ミラクルスペース": (2.4, "味方全体"),   # 4属性以上での攻撃時
    "テンペスト": (1.15, "味方全体"),        # 敵の状態異常1つにつき+15%（最大+90%）
}


def download_csv(gid):
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"
    print(f"ダウンロード中... gid={gid}")
    with urllib.request.urlopen(url) as res:
        text = res.read().decode("utf-8")
    return list(csv.reader(io.StringIO(text)))


def main():
    skill_rows = download_csv(GID_SKILL)[1:]
    stat_rows = download_csv(GID_STAT)

    # --- スキルDB: (名称, レア度) ごとに1件にまとめる ---
    entries = {}
    for r in skill_rows:
        if len(r) < 30 or not r[1].strip():
            continue
        key = (r[1].strip(), r[0].strip())
        e = entries.setdefault(key, {
            "n": r[1].strip(),      # 名称
            "r": r[0].strip(),      # レア度
            "t": r[5].strip(),      # タイプ
            "m": int(r[6] or 0),    # 主属性 1-5
            "s": int(r[7] or 0),    # 副属性 0=なし
            "u": r[8].strip(),      # 画像URL
        })
        kind, text = r[16].strip(), r[29].strip()
        if kind == "NS" and "ns" not in e and text:
            e["ns"] = text
        if kind == "LS" and "ls" not in e and text:
            e["ls"] = text

        # リーダースキル倍率 [体力, 攻撃, 回復] とその範囲（列11-14）
        if "lm" not in e:
            try:
                lm = [round(float(r[11]), 3), round(float(r[12]), 3), round(float(r[13]), 3)]
                if any(lm):
                    e["lm"] = lm
                    e["lr"] = r[14].strip()
            except ValueError:
                pass

        # ダメージ計算に使える倍率スキルを抽出（列17-21: 大分類/小分類/範囲/倍率）
        # 「発動率50%」「攻撃ダウン80%」など倍率でないものは除外する
        dai, syo = r[17].strip(), r[18].strip()
        name_map = None
        if "エンハ" in dai or "攻撃値up" in dai:          # エンハンス/条件付きエンハンス/攻撃値up
            name_map = syo or dai
        elif dai == "与ダメアップ":                        # キラースキル系
            name_map = "与ダメアップ" + ("(確率)" if "確率" in syo else "")
        elif dai == "デバフ" and ("被ダメ" in syo or syo == "盾破壊"):
            name_map = syo
        elif dai == "その他" and syo in ("プリズム効果アップ", "クリティカル倍率up"):
            name_map = syo
        # 状態異常（怒り/怯え/脱力/麻痺）: 敵に付与するとダメージ増
        if dai == "状態異常" and syo in AILMENT_MAP:
            ail = e.setdefault("ail", [])
            if not any(a["k"] == syo for a in ail):
                ail.append({"k": syo, "v": AILMENT_MAP[syo]})

        # フィールド効果: 倍率がわかっているものだけ倍率行として追加
        if dai == "フィールド効果" and syo in FIELD_MAP:
            fv, fr = FIELD_MAP[syo]
            sk = e.setdefault("sk", [])
            fk = f"フィールド:{syo}"
            if not any(s["k"] == fk for s in sk):
                sk.append({"v": fv, "r": fr, "k": fk, "t": text})

        if name_map:
            try:
                v = round(float(r[20]), 3)
                if syo == "クリティカル倍率up" and v >= 5:
                    v = round(1 + v / 100, 3)              # 「50」→「1.5倍」に換算
                if v > 1:
                    sk = e.setdefault("sk", [])
                    # 同名スキルはフルパワー版などの重複 → 小さい方＝通常版を残す
                    dup = next((s for s in sk if s["k"] == name_map), None)
                    if dup:
                        if v < dup["v"]:
                            dup["v"] = v
                            dup["t"] = text
                    else:
                        sk.append({"v": v, "r": r[19].strip(), "k": name_map, "t": text})
            except ValueError:
                pass

    # --- ステDB: 「最大ステータス(とっくん込み)」ブロックから攻撃力などを拾う ---
    # 列32=名称, 列34=★n, 列35=体力極, 列36=攻撃極, 列37=回復極
    stats = {}
    for r in stat_rows:
        if len(r) > 37 and r[32].strip() and r[34].strip().startswith("★"):
            name = norm(r[32].strip())
            star = r[34].strip().lstrip("★")
            try:
                stats[(name, star)] = (int(r[35]), int(r[36]), int(r[37]))
            except ValueError:
                pass

    # --- ステDB: 「Lv.MAX」ブロックからレア度ごとの素のステータスを拾う ---
    # 列2,6,10,14,... に「★n」ラベル、その右3列が 体力/攻撃/回復（とっくん分は含まない）
    lvmax = {}
    for r in stat_rows:
        if len(r) > 30 and r[1].strip():
            name = norm(r[1].strip())
            for ci in (2, 6, 10, 14, 18, 22, 26):
                if ci + 3 < len(r) and r[ci].strip().startswith("★"):
                    star = r[ci].strip().lstrip("★")
                    try:
                        lvmax[(name, star)] = (int(r[ci + 1]), int(r[ci + 2]), int(r[ci + 3]))
                    except ValueError:
                        pass

    # スキルDBのカードに攻撃力をくっつける
    # とっくん込み最大値があればそれを優先、なければLv.MAXの素の値（x=1の印つき）
    matched = matched_lv = 0
    for (raw_name, star), e in entries.items():
        name = norm(raw_name)
        if (name, star) in stats:
            hp, atk, rec = stats[(name, star)]
            e["a"] = atk   # 攻撃(とっくん最大)
            e["h"] = hp    # 体力
            e["c"] = rec   # 回復
            matched += 1
        elif (name, star) in lvmax:
            hp, atk, rec = lvmax[(name, star)]
            e["a"] = atk
            e["h"] = hp
            e["c"] = rec
            e["x"] = 1     # とっくん分を含まないLv.MAX値の印
            matched_lv += 1

    cards = sorted(entries.values(), key=lambda e: (e["n"], e["r"]))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "carddb.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("const CARD_DB = " + json.dumps(cards, ensure_ascii=False, separators=(",", ":")) + ";\n")

    print(f"完成! カード数: {len(cards)}（とっくん込み攻撃力: {matched}、Lv.MAX素値: {matched_lv}）")
    print(f"サイズ: {os.path.getsize(out) / 1024 / 1024:.2f} MB → {out}")


if __name__ == "__main__":
    main()
