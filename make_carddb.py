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
import re
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

# フィールド効果の攻撃倍率（分類シートの補足タブ調べ）: (倍率, 対象範囲)
FIELD_MAP = {
    "晴れ": (1.5, "赤"), "雨": (1.5, "青"), "風": (1.5, "緑"),
    "雷": (1.5, "黄"), "雪": (1.5, "紫"),
    "ミラクルスペース": (2.4, "味方全体"),   # スタメン4色以上・属性相性無効
    "ラブリーオーラ": (1.5, "味方全体"),     # スタメン3色以上
    "テンペスト": (1.15, "味方全体"),        # 敵の状態異常1種類につき+15%（上限90%）
    "無量空処": (10, "自身のみ"),            # 五条悟の攻撃10倍
    "いばらのサーカス": (10, "自身のみ"),    # 戦乙女ドッペルゲンガーアルルの攻撃10倍
    "やみいろパラダイス": (10, "自身のみ"),  # 闇の貴公子サタン＆カーバンクルの攻撃10倍
    "ノーティカルスター": (1.7, "味方全体"), # ほしさゆるクルークがいるとき
    "南国日和": (1.7, "味方全体"),           # なつぞらのアマノネがいるとき
    "ハピネスオーラ": (1.5, "味方全体"),     # ハッピーフェアリーアミティがいるとき
    "マジカルファンタジア": (5, "味方全体"), # サーカスの奇術師アルル＆カーバンクルがいるとき(セガ公式)
}

# モード効果の攻撃倍率（分類シートの補足タブ調べ）: (倍率, 対象範囲)
MODE_MAP = {
    "りんごノリノリモード": (2, "自身のみ"),      # 与ダメ2倍
    "ラファエルモード": (5, "自身のみ"),          # 自身の攻撃5倍
    "レガムントリベンジモード": (7, "自身のみ"),  # 与ダメ7倍
    "ルーワどきどきモード": (1, "味方全体"),      # 攻撃倍率なし・副属性等倍化のみ(s3で表現)
}


# ---------- スキル分類（ゲーム内スキル検索と同じカテゴリ） ----------
# 図鑑の絞り込みプルダウンに使う。並び順はゲーム内のスキル検索画面と同じ。
SKILL_CAT_GROUPS = [
    ("攻撃力・回復力・ダメージアップ", [
        "攻撃力アップ", "回復力アップ", "条件達成で攻撃力アップ",
        "相手に与えるダメージアップ", "相手が受けるダメージアップ",
        "相手が受ける属性ダメージアップ", "攻撃値アップ＆副属性等倍化"]),
    ("攻撃変化", ["全体攻撃化", "連続攻撃化", "爆裂攻撃化", "タフネス貫通化"]),
    ("ぷよ消し攻撃強化", ["どの色ぷよの連鎖でも攻撃が発生", "色ぷよを消した時に発生する数値アップ"]),
    ("特殊スキル1", ["なぞり消し数アップ 同時消し係数アップ", "とくべつルール発動", "属性盾弱体化"]),
    ("特殊スキル2", ["攻撃属性変化", "チャージ"]),
    ("攻撃スキル1", ["色ぷよ変換", "その他（攻撃スキル1）"]),
    ("攻撃スキル2", ["攻撃力参照", "体力参照", "回復力参照", "その他（攻撃スキル2）"]),
    ("状態異常", ["怒り", "怯え", "混乱", "麻痺", "毒", "封印", "やどり木", "脱力", "まやかし", "凍結"]),
    ("サブ特殊", ["フィールド効果展開", "モード", "累積で相手に与えるダメージアップ"]),
    ("変換", [
        "連鎖のタネ", "チャンスぷよ変換", "プラス状態", "ネクストぷよ変換", "ネクストプラス状態",
        "ハートBOX変換", "プリズムボール変換", "おじゃまぷよ変換", "ぬりかえ",
        "だいれんさチャンス発動", "条件達成で落ちてくる色ぷよを変換"]),
    ("回復・解除", ["体力回復", "状態異常解除", "攻撃力・回復力ダウン解除", "復活"]),
    ("防御・ターンプラス", ["バリア", "反射", "かばう", "属性盾", "攻撃力ダウン", "ターンプラス", "状態異常ターンプラス"]),
    ("スキル加速", ["スキル発動ぷよ数減少", "どの色ぷよを消してもスキル発動ぷよ数減少"]),
    ("その他", ["フィールドリセット", "スキルを再度発動可能", "デッキスクロール", "カウンター", "相手の状態異常を解除"]),
]
SKILL_CATS = [c for _, cats in SKILL_CAT_GROUPS for c in cats]
CAT_ID = {c: i for i, c in enumerate(SKILL_CATS)}

STATUS_AILS = {"怒り", "怯え", "混乱", "麻痺", "毒", "封印", "やどり木", "脱力", "まやかし", "凍結"}

# シートの (大分類, 小分類) → ゲーム内カテゴリ の対応表（ユーザー確認済み）
PAIR_MAP = {
    ("エンハンス", "攻撃エンハ"): ["攻撃力アップ"],
    ("エンハンス", "回復エンハ"): ["回復力アップ"],
    ("デバフ", "被ダメアップ"): ["相手が受けるダメージアップ"],
    ("デバフ", "指定属性被ダメアップ"): ["相手が受ける属性ダメージアップ"],
    ("デバフ", "攻撃ダウン"): ["攻撃力ダウン"],
    ("デバフ", "ターンプラス"): ["ターンプラス"],
    ("デバフ", "盾破壊"): ["属性盾弱体化"],
    ("攻撃変化(連撃化など)", "連撃化"): ["連続攻撃化"],
    ("攻撃変化(連撃化など)", "全体攻撃化"): ["全体攻撃化"],
    ("攻撃変化(連撃化など)", "爆裂化"): ["爆裂攻撃化"],
    ("攻撃変化(連撃化など)", "タフ貫化"): ["タフネス貫通化"],
    ("攻撃変化(連撃化など)", "属性変化"): ["攻撃属性変化"],
    ("ワイルド/数値up", "ワイルド化"): ["どの色ぷよの連鎖でも攻撃が発生"],
    ("ワイルド/数値up", "数値アップ"): ["色ぷよを消した時に発生する数値アップ"],
    ("係数up", "同時消し係数n倍"): ["なぞり消し数アップ 同時消し係数アップ"],
    ("係数up", "連鎖係数n倍"): [],  # ゲーム内検索に該当カテゴリなし
    ("属性攻撃付加効果", "無属性攻撃"): ["その他（攻撃スキル2）"],
    ("属性攻撃付加効果", "タフネス貫通攻撃"): [],  # 攻撃本体の行(攻撃参照など)で分類済み
    ("属性攻撃付加効果", "爆裂攻撃"): [],
    ("盤面変換", "チャンスぷよ生成"): ["チャンスぷよ変換"],
    ("盤面変換", "ぷよ色変換"): ["色ぷよ変換"],
    ("盤面変換", "任意ぷよ変換"): ["色ぷよ変換"],
    ("盤面変換", "フィールドリセット"): ["フィールドリセット"],
    ("盤面変換", "プリズム生成"): ["プリズムボール変換"],
    ("盤面変換", "任意プリズム変換"): ["プリズムボール変換"],
    ("盤面変換", "プリズム砲"): ["プリズムボール変換"],
    ("盤面変換", "プラス生成"): ["プラス状態"],
    ("盤面変換", "任意プラス変換"): ["プラス状態"],
    ("盤面変換", "連鎖のタネ"): ["連鎖のタネ"],
    ("盤面変換", "ハート生成"): ["ハートBOX変換"],
    ("盤面変換", "任意ハート変換"): ["ハートBOX変換"],
    ("盤面変換", "落下ぷよ変換"): ["条件達成で落ちてくる色ぷよを変換"],
    ("とくべつルール", "ぬりかえ"): ["ぬりかえ"],
    ("とくべつルール", "3個で消える"): ["とくべつルール発動"],
    ("その他(状態異常関連)", "状態異常解除"): ["状態異常解除"],
    ("その他(状態異常関連)", "自動状態異常解除"): ["状態異常解除"],
    ("その他(状態異常関連)", "攻撃・回復ダウン解除"): ["攻撃力・回復力ダウン解除"],
    ("その他(状態異常関連)", "状態異常延伸"): ["状態異常ターンプラス"],
    ("防御系", "バリア"): ["バリア"],
    ("防御系", "反射"): ["反射"],
    ("防御系", "かばう"): ["かばう"],
    ("防御系", "属性盾"): ["属性盾"],
    ("防御系", "カウンター"): ["カウンター"],
    ("その他", "スキル再発動"): ["スキルを再度発動可能"],
    ("その他", "チャージ"): ["チャージ"],
    ("その他", "大連鎖チャンス"): ["だいれんさチャンス発動"],
    ("その他", "大連鎖チャンスFV"): ["だいれんさチャンス発動"],
    ("その他", "蘇生"): ["復活"],
    ("その他", "スライド"): ["デッキスクロール"],
    ("その他", "フィールド効果解除"): [],  # ゲーム内検索に該当カテゴリなし
    ("その他", "攻撃回復逆転"): [],        # 〃
    # デメリット系は基本分類なし。以下の2つだけ拾う（ユーザー確認済み）
    ("デメリット系", "相手に継続状態異常解除"): ["相手の状態異常を解除"],
    ("デメリット系", "おじゃまぷよ生成"): ["おじゃまぷよ変換"],
}

# 大分類ごと一律で対応するもの
DAI_MAP = {
    "攻撃値up&副属性等倍化": ["攻撃値アップ＆副属性等倍化"],
    "条件付きエンハンス": ["条件達成で攻撃力アップ"],
    "属性攻撃 / 攻撃参照": ["攻撃力参照"],
    "属性攻撃 / 体力参照": ["体力参照"],
    "属性攻撃 / 回復参照": ["回復力参照"],
    "属性攻撃 / 固定ダメージ": ["その他（攻撃スキル2）"],
    "属性攻撃 / その他": ["その他（攻撃スキル2）"],
    "追加攻撃": ["その他（攻撃スキル2）"],
    "なぞり消し数増加": ["なぞり消し数アップ 同時消し係数アップ"],
    "即時回復": ["体力回復"],
    "自動回復": ["体力回復"],
    "フィールド効果": ["フィールド効果展開"],
    "モード効果": ["モード"],
    "複色スキルカウント": ["どの色ぷよを消してもスキル発動ぷよ数減少"],
}

# 分類しないと決めた大分類（警告を出さないためのリスト）
NO_CLASS_DAI = {"スキル砲強化", "スキルなし", "デメリット系", "開幕発動数減少"}

unknown_combos = {}  # 対応表にない組み合わせの記録（実行時に警告表示）


def classify_row(dai, syo, text, warn=True):
    """シートの大分類/小分類（＋効果文）をゲーム内スキル検索のカテゴリ名リストにする"""
    if (dai, syo) in PAIR_MAP:
        return PAIR_MAP[(dai, syo)]
    # 効果文を見て振り分けるもの
    if dai == "与ダメアップ":
        return ["累積で相手に与えるダメージアップ" if "累積" in text else "相手に与えるダメージアップ"]
    if dai == "盤面変換" and syo == "ネクスト変換":
        return ["ネクストプラス状態" if "プラス" in text else "ネクストぷよ変換"]
    if dai == "盤面変換" and syo == "ぷよ消去":
        # ぷよを消す系は「その他（攻撃スキル1）」。キッチンシリーズ（スキル発動ぷよ数を
        # 減らすおまけ付き）はスキル加速にも入れる（ユーザー確認済み）
        cats = ["その他（攻撃スキル1）"]
        if "スキル発動ぷよ数" in text and "減ら" in text:
            cats.append("スキル発動ぷよ数減少")
        return cats
    if dai == "発動数減少":
        return ["どの色ぷよを消してもスキル発動ぷよ数減少" if "どの色ぷよ" in text else "スキル発動ぷよ数減少"]
    if dai == "状態異常" and syo in STATUS_AILS:
        return [syo]
    if dai in DAI_MAP:
        return DAI_MAP[dai]
    if dai in NO_CLASS_DAI:
        return []
    if warn and (dai or syo):
        unknown_combos[(dai, syo)] = unknown_combos.get((dai, syo), 0) + 1
    return []


def cat_ids(dai, syo, text):
    """倍率スキル(sk)エントリに付けるスキル分類ID（見つからなければNone）"""
    cats = classify_row(dai, syo, text, warn=False)
    return [CAT_ID[c] for c in cats] if cats else None


# ダブルパワースキルを持つカード（Wikiはフルパワー欄に載せているので付け替える対象）
# 現状この2枚だけ（ユーザー確認済み2026-07-12）。新しいダブルパワー持ちが出たらここに追加
WP_CARDS = {"サタン＆エコロ", "ドラコ＆リデル"}

# Wiki由来カード（シートに分類がない）用: 効果文からの推測分類
TEXT_RULES = [
    (r"だいれんさチャンス", "だいれんさチャンス発動"),
    (r"チャンスぷよに", "チャンスぷよ変換"),
    (r"連鎖のタネ", "連鎖のタネ"),
    (r"プリズムボール", "プリズムボール変換"),
    (r"ハートBOX", "ハートBOX変換"),
    (r"おじゃまぷよに変え", "おじゃまぷよ変換"),
    (r"ぬりかえ", "ぬりかえ"),
    (r"ネクストぷよを.*プラス", "ネクストプラス状態"),
    (r"ネクストぷよを.*変え", "ネクストぷよ変換"),
    (r"プラス状態に", "プラス状態"),
    (r"ぷよ(と.{1,8})?を(すべて|ランダムで)?.{0,10}ぷよに変え", "色ぷよ変換"),
    (r"「こうげき」×", "攻撃力参照"),
    (r"「たいりょく」×", "体力参照"),
    (r"「かいふく」×", "回復力参照"),
    (r"体力を.{0,15}回復", "体力回復"),
    (r"バリア", "バリア"),
    (r"反射", "反射"),
    (r"かばう", "かばう"),
    (r"なぞり消(し数|せる数)", "なぞり消し数アップ 同時消し係数アップ"),
    (r"同時消し係数", "なぞり消し数アップ 同時消し係数アップ"),
    (r"どの色ぷよの連鎖でも攻撃", "どの色ぷよの連鎖でも攻撃が発生"),
    (r"全体攻撃に", "全体攻撃化"),
    (r"連続攻撃に", "連続攻撃化"),
    (r"爆裂攻撃に", "爆裂攻撃化"),
    (r"属性攻撃に(変え|する|なる)", "攻撃属性変化"),
    (r"フィールドをリセット", "フィールドリセット"),
    (r"スキルを再度発動可能", "スキルを再度発動可能"),
    # 「落ちてくる色ぷよを変換」はゲーム内では条件付き（全消し達成時など）のカテゴリなので、
    # シェゾのような無条件（リセット直後）の変換には付けない（ユーザー確認済み）
    (r"受ける.{1,12}?属性ダメージ", "相手が受ける属性ダメージアップ"),
    (r"受けるダメージを", "相手が受けるダメージアップ"),
    (r"回復力を[\d.]+倍", "回復力アップ"),
    (r"復活", "復活"),
]


def classify_text(text):
    """効果文だけからの推測分類（Wiki由来カード用・確実なパターンのみ）"""
    if not text:
        return []
    ids = set()
    for pat, cat in TEXT_RULES:
        if re.search(pat, text):
            ids.add(CAT_ID[cat])
    # 与ダメアップ: 回数を重ねて増えるもの（ドラコ＆リデル等）は「累積で〜」
    if "与えるダメージを" in text:
        if "累積" in text or re.search(r"回数×", text):
            ids.add(CAT_ID["累積で相手に与えるダメージアップ"])
        else:
            ids.add(CAT_ID["相手に与えるダメージアップ"])
    # 攻撃力n倍: 条件付き（同時消し/同時攻撃/連鎖）なら「条件達成で〜」
    if re.search(r"攻撃力[をが]?.{0,20}[\d.]+倍", text):
        if re.search(r"同時消し|同時攻撃|連鎖以上|以上の連鎖", text):
            ids.add(CAT_ID["条件達成で攻撃力アップ"])
        else:
            ids.add(CAT_ID["攻撃力アップ"])
    # 状態異常: 敵にかけるものだけ（味方への封印などは対象外）
    if "相手" in text or "敵" in text:
        for a in STATUS_AILS:
            if re.search(rf"「?{a}」?状態にす", text):
                ids.add(CAT_ID[a])
    if "状態異常" in text and re.search(r"解除|回復", text):
        ids.add(CAT_ID["相手の状態異常を解除" if "相手の状態異常" in text else "状態異常解除"])
    if "スキル発動ぷよ数" in text and "減" in text:
        ids.add(CAT_ID["どの色ぷよを消してもスキル発動ぷよ数減少" if "どの色ぷよ" in text
                       else "スキル発動ぷよ数減少"])
    return sorted(ids)


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
        if not r[0].strip():
            continue  # レア度が空の行はコラボ区切り等の見出し行でカードではない
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
        # 列9「FP(+)」: 空=通常、(FP)=フルパワー（発動ぷよ数35→50）、
        # (WP)=ダブルパワー（発動が2色になる。サタン＆エコロなど）、
        # (DS)=モード発動などを含む現行スキルの行、
        # (+)/SPなど=スキルプラス等の強化版。強化版の行はまるごと使わない
        # （以前は2つ目の効果文をすべてフルパワーと誤認していた）
        variant = r[9].strip()
        if variant not in ("", "(FP)", "(DS)", "(WP)"):
            continue
        is_fp = variant == "(FP)"
        is_wp = variant == "(WP)"
        if kind == "NS" and text:
            if is_fp:
                if "fp" not in e:
                    e["fp"] = text                 # (FP)行＝フルパワースキル
            elif is_wp:
                if "wp" not in e:
                    e["wp"] = text                 # (WP)行＝ダブルパワースキル
            elif "ns" not in e:
                e["ns"] = text                     # 通常行＝ノーマルスキル
        if kind == "LS" and not is_fp and not is_wp and "ls" not in e and text:
            e["ls"] = text
        if kind == "TS" and not is_fp and not is_wp and "ts" not in e and text:
            e["ts"] = text   # とくもりスキル（カード詳細表示用）
        if kind == "AB" and not is_fp and not is_wp and "ab" not in e and text:
            e["ab"] = text   # アビリティ（カード詳細表示用）

        # リーダースキル倍率 [体力, 攻撃, 回復] とその範囲（列11-14）
        if "lm" not in e and not is_fp and not is_wp:
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

        # スキル分類（ゲーム内スキル検索と同じカテゴリ）:
        # 通常スキル=sc、フルパワー=fc、ダブルパワー=wc
        if kind == "NS":
            cats = classify_row(dai, syo, text)
            if cats:
                key = "_fc" if is_fp else ("_wc" if is_wp else "_sc")
                e.setdefault(key, set()).update(CAT_ID[c] for c in cats)
        if is_wp:
            continue  # ダブルパワー行は効果文と分類だけ使う（倍率などは通常行から）
        name_map = None
        if ("エンハ" in dai or "攻撃値up" in dai) and "回復" not in syo and "体力エンハ" not in syo:
            # エンハンス/条件付きエンハンス/攻撃値up（回復・体力エンハはダメージと無関係なので除外）
            name_map = syo or dai
        elif dai == "与ダメアップ":                        # キラースキル系
            name_map = "与ダメアップ" + ("(確率)" if "確率" in syo else "")
        elif dai == "デバフ" and ("被ダメ" in syo or syo == "盾破壊"):
            name_map = syo
        elif dai == "その他" and syo in ("プリズム効果アップ", "クリティカル倍率up"):
            name_map = syo
        # 状態異常（怒り/怯え/脱力/麻痺）: 敵に付与するとダメージ増
        # リダスキの「開幕怯え」も怯えとして扱う（LS由来はリーダー配置時のみ有効の印を付ける）
        ail_key = syo if dai == "状態異常" else ("怯え" if syo == "開幕怯え" else None)
        if ail_key in AILMENT_MAP:
            ail = e.setdefault("ail", [])
            dup = next((a for a in ail if a["k"] == ail_key), None)
            if dup:
                if dup.get("o") == "LS" and kind != "LS":
                    dup.pop("o", None)  # スキルでも付与できるならLS限定を外す
            else:
                a = {"k": ail_key, "v": AILMENT_MAP[ail_key]}
                if kind == "LS":
                    a["o"] = "LS"
                ail.append(a)

        # フィールド効果・モード効果: 倍率がわかっているものだけ倍率行として追加
        if dai == "フィールド効果" and syo in FIELD_MAP:
            fv, fr = FIELD_MAP[syo]
            sk = e.setdefault("sk", [])
            fk = f"フィールド:{syo}"
            if not any(s["k"] == fk for s in sk):
                fe = {"v": fv, "r": fr, "k": fk, "t": text}
                if kind == "LS":
                    fe["o"] = "LS"
                cats = cat_ids(dai, syo, text)  # スキル分類ID（倍率行に表示）
                if cats:
                    fe["c"] = cats
                sk.append(fe)
        if dai == "モード効果" and syo in MODE_MAP:
            mv, mr = MODE_MAP[syo]
            sk = e.setdefault("sk", [])
            if not any(s["k"] == syo for s in sk):
                me = {"v": mv, "r": mr, "k": syo, "t": text}
                if kind == "LS":
                    me["o"] = "LS"
                if syo == "ルーワどきどきモード":
                    me["s3"] = 1  # 副属性等倍化＝副属性セルに3倍の値を入れる印
                cats = cat_ids(dai, syo, text)
                if cats:
                    me["c"] = cats
                sk.append(me)

        if name_map:
            try:
                v = round(float(r[20]), 3)
                if syo == "クリティカル倍率up" and v >= 5:
                    v = round(1 + v / 100, 3)              # 「50」→「1.5倍」に換算
                if v > 1:
                    # 隣接エンハ（影山飛雄など）: 行名を「攻撃エンハ(隣接)」に統一し、
                    # 範囲をアプリが解釈できる表記に揃える
                    rng = r[19].strip()
                    if "隣接" in rng:
                        if name_map == "攻撃エンハ":
                            name_map = "攻撃エンハ(隣接)"
                        rng = {"隣接内": "隣接",          # 自身と両隣
                               "隣接内同色": "隣接同色",   # 自身と、両隣のうち同色のカード
                               "隣接": "両隣のみ",         # 自身を含まない（シャイニールミナス等）
                               }.get(rng, rng)
                    # 攻撃値up&副属性等倍化（サタン＆エコロ）: 副属性係数が固定1/3のため、
                    # 等倍化は副属性セルに3倍の値を入れて表現する印(s3)を付ける
                    s3 = name_map == "攻撃値up&副属性等倍化"
                    # 指定属性被ダメアップ（アリィ＆ラフィソル等）: 効果文から対象の色を取り、
                    # 主属性/副属性それぞれのダメージ属性で判定する印(pa)を付ける
                    pa = False
                    if name_map == "指定属性被ダメアップ":
                        m2 = re.search(r"受ける(.{1,12}?)属性ダメージ", text)
                        cols = [c for c in "赤青緑黄紫" if m2 and c in m2.group(1)]
                        if cols:
                            rng = "".join(cols)
                            pa = True
                    sk = e.setdefault("sk", [])
                    # 同名スキルの重複はノーマル/フルパワーの違い
                    # → 小さい方=通常(v)、大きい方=フルパワー(fv) として両方残す
                    dup = next((s for s in sk if s["k"] == name_map), None)
                    if dup:
                        vals = [dup["v"], dup.get("fv", dup["v"]), v]
                        lo, hi = min(vals), max(vals)
                        dup["v"] = lo
                        if hi > lo:
                            dup["fv"] = hi
                            if v == hi:
                                dup["ft"] = text  # フルパワー版の効果文
                    else:
                        se = {"v": v, "r": rng, "k": name_map, "t": text}
                        if kind == "LS":
                            se["o"] = "LS"  # リーダー/サポート配置時のみ有効
                        if pa:
                            se["pa"] = 1
                        if s3:
                            se["s3"] = 1
                        cats = cat_ids(dai, syo, text)
                        if cats:
                            se["c"] = cats
                        sk.append(se)
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

    # スキル分類の作業用セットを保存用の配列に変換する
    for e in entries.values():
        for key in ("sc", "fc", "wc"):
            s = e.pop("_" + key, None)
            if s:
                e[key] = sorted(s)

    # 対応表にない分類があれば知らせる（新しい分類がシートに増えたときに気づける）
    if unknown_combos:
        print("⚠ 対応表にないスキル分類がありました（分類されません）:")
        for (dai, syo), cnt in sorted(unknown_combos.items(), key=lambda x: -x[1]):
            print(f"   {dai} | {syo} … {cnt}件")

    # --- Puyo Nexus Wikiから取得したカード（nexus_cards.json）を合体 ---
    # シートに同じ(名前, レア度)がある場合はシートのデータを優先する
    nexus_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nexus_cards.json")
    nexus_added = 0
    if os.path.exists(nexus_path):
        with open(nexus_path, encoding="utf-8") as f:
            nexus = json.load(f)
        existing = {(norm(e["n"]), e["r"]) for e in entries.values()}
        # 同名カードがWikiに複数ある場合、情報量の多いほうを優先する
        nexus = sorted(nexus, key=lambda c: -len(c))
        for card in nexus:
            key = (norm(card["n"]), card["r"])
            if key in existing:
                continue
            # ステータスもスキルもないカード（強化素材など）は除外
            if not (card.get("a") or card.get("ns") or card.get("ls")):
                continue
            existing.add(key)
            entry = {k: v for k, v in card.items() if k != "code"}
            # ダブルパワー持ちカード: Wikiではフルパワー欄に入っているので付け替える
            # （フルパワー=発動ぷよ数35→50、ダブルパワー=発動が2色になる別システム）
            if card["n"] in WP_CARDS and entry.get("fp"):
                entry["wp"] = entry.pop("fp")
                for s in entry.get("sk", []):
                    if "fv" in s:
                        s["wv"] = s.pop("fv")   # ダブルパワー倍率（計算機では未使用）
                    if "ft" in s:
                        s["wt"] = s.pop("ft")
            # Wiki由来はシートの分類がないので、効果文から推測して分類する
            for src_key, dst_key in (("ns", "sc"), ("fp", "fc"), ("wp", "wc")):
                cats = classify_text(entry.get(src_key, ""))
                if cats:
                    entry[dst_key] = cats
            # Wiki由来の指定属性被ダメアップ（受ける◯属性ダメージをn倍）: 効果文から拾う
            # （汎用の「受けるダメージをn倍」の正規表現では拾えないため行が欠けていた）
            m = re.search(r"受ける(.{1,12}?)属性ダメージを([\d.]+)倍", entry.get("ns", ""))
            if m:
                cols = [c for c in "赤青緑黄紫" if c in m.group(1)]
                if cols and not any(s.get("k") == "指定属性被ダメアップ" for s in entry.get("sk", [])):
                    se = {"v": float(m.group(2)), "r": "".join(cols), "k": "指定属性被ダメアップ",
                          "t": entry.get("ns", ""), "pa": 1,
                          "c": [CAT_ID["相手が受ける属性ダメージアップ"]]}
                    mf = re.search(r"受ける.{1,12}?属性ダメージを([\d.]+)倍", entry.get("fp", ""))
                    if mf and float(mf.group(1)) > se["v"]:
                        se["fv"] = float(mf.group(1))
                        se["ft"] = entry.get("fp", "")
                    entry.setdefault("sk", []).append(se)
            # Wiki由来の倍率付き攻撃属性変化（ゆめいろシーフのシェゾ:
            # 「通常攻撃をn倍の◯属性攻撃に変える」）: 全カードに倍率を入れる行を作る
            m = re.search(r"通常攻撃を([\d.]+)倍の[赤青緑黄紫自]属性攻撃に変え", entry.get("ns", ""))
            if m and not any(s.get("k") == "攻撃属性変化" for s in entry.get("sk", [])):
                entry.setdefault("sk", []).append({
                    "v": float(m.group(1)), "r": "味方全体", "k": "攻撃属性変化",
                    "t": entry.get("ns", ""), "c": [CAT_ID["攻撃属性変化"]]})
            # Wiki由来の攻撃値up&副属性等倍化: 副属性セル3倍の印を付ける
            for s in entry.get("sk", []):
                if s.get("k") == "攻撃値up&副属性等倍化":
                    s["s3"] = 1
            # Wiki由来の隣接エンハ: 効果文から「自身を含むか」「同色限定か」を判定して範囲を揃える
            for s in entry.get("sk", []):
                if s.get("k") == "攻撃エンハ(隣接)":
                    t = s.get("t", "")
                    if re.search(r"隣接する[赤青緑黄紫]属性", t):
                        s["r"] = "隣接同色"          # 自身と、両隣のうち同色のカード
                    elif "このカードと" in t:
                        s["r"] = "隣接"              # 自身と両隣
                    else:
                        s["r"] = "両隣のみ"          # 自身を含まない
            # Wiki由来の倍率スキルにも行の名前から分類IDを付ける
            for s in entry.get("sk", []):
                for pat, cat in (("攻撃エンハ", "攻撃力アップ"), ("同時攻撃", "条件達成で攻撃力アップ"),
                                 ("属性を含む", "条件達成で攻撃力アップ"), ("連鎖以上", "条件達成で攻撃力アップ"),
                                 ("与ダメ", "相手に与えるダメージアップ"), ("被ダメ", "相手が受けるダメージアップ"),
                                 ("盾破壊", "属性盾弱体化")):
                    if pat in s.get("k", ""):
                        s["c"] = [CAT_ID[cat]]
                        break
            entries[(card["n"] + "@nexus", card["r"])] = entry
            nexus_added += 1
        print(f"Wiki由来のカードを追加: {nexus_added}枚")

    cards = sorted(entries.values(), key=lambda e: (e["n"], e["r"]))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "carddb.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("const CARD_DB = " + json.dumps(cards, ensure_ascii=False, separators=(",", ":")) + ";\n")
        # スキル分類の名前一覧とグループ分け（図鑑のプルダウン用）
        f.write("const SKILL_CATS = " + json.dumps(SKILL_CATS, ensure_ascii=False, separators=(",", ":")) + ";\n")
        groups = [[g, [CAT_ID[c] for c in cats]] for g, cats in SKILL_CAT_GROUPS]
        f.write("const SKILL_CAT_GROUPS = " + json.dumps(groups, ensure_ascii=False, separators=(",", ":")) + ";\n")

    print(f"完成! カード数: {len(cards)}（とっくん込み攻撃力: {matched}、Lv.MAX素値: {matched_lv}）")
    print(f"サイズ: {os.path.getsize(out) / 1024 / 1024:.2f} MB → {out}")

    # 人間が読める形（CSV）でも出力する。Googleスプレッドシートにインポート可能
    csv_out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "carddb.csv")
    with open(csv_out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["名称", "レア度", "タイプ", "主属性", "副属性", "攻撃", "体力", "回復",
                    "とっくん", "出典", "リダ攻倍率", "倍率スキル", "付与できる状態異常",
                    "スキル", "スキル分類", "フルパワー分類", "ダブルパワー分類",
                    "リーダースキル", "とくもり", "きらめきオーラ"])
        for e in cards:
            w.writerow([
                e["n"], "★" + e["r"], e.get("t", ""),
                COLOR.get(e.get("m"), ""), COLOR.get(e.get("s"), ""),
                e.get("a", ""), e.get("h", ""), e.get("c", ""),
                ("込み" if e.get("a") and not e.get("x") else ("なし" if e.get("a") else "")),
                ("Wiki" if e.get("w") else "シート"),
                (e.get("lm") or [0, "", 0])[1] if e.get("lm") else "",
                "; ".join(f"{s['k']}×{s['v']}{'(LS時のみ)' if s.get('o') == 'LS' else ''}" for s in e.get("sk", [])),
                "; ".join(f"{a['k']}×{a['v']}" for a in e.get("ail", [])),
                e.get("ns", ""),
                "; ".join(SKILL_CATS[i] for i in e.get("sc", [])),
                "; ".join(SKILL_CATS[i] for i in e.get("fc", [])),
                "; ".join(SKILL_CATS[i] for i in e.get("wc", [])),
                e.get("ls", ""), e.get("ts", ""), e.get("ga", ""),
            ])
    print(f"CSV版も出力: {csv_out}")


if __name__ == "__main__":
    main()
