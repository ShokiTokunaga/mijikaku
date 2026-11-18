# 検出ツール

## 環境の確認と初回準備

OS・シェルを確認し、`python3`・`python`・`py -3`などからPython 3.12以上の実行コマンドを選ぶ。Python・venv・pipが不足する場合は、その環境の導入方法で準備する。

専用の仮想環境を、スキルの配置先とは別の書き込み可能な場所に置く。既存なら、そのPythonが3.12以上で、`-m pip --version`が成功することを確認して再利用する。

環境の作成は初回、依存の導入は初回・依存不足時・`requirements.txt`更新時に行う。

以下はコマンド例。Pythonのコマンドと絶対パスは実行環境に合わせて選ぶ。スキル配置先は、使用中の`SKILL.md`があるディレクトリを指す。

Linux・macOSのsh系シェル：

```bash
python3 --version
python3 -m venv "/作業場所/mijikaku-venv"
"/作業場所/mijikaku-venv/bin/python" -m pip --version
"/作業場所/mijikaku-venv/bin/python" -m pip install -r "/スキル配置先/mijikaku/scripts/requirements.txt"
```

WindowsのPowerShell：

```powershell
py -3 --version
py -3 -m venv "C:\作業場所\mijikaku-venv"
& "C:\作業場所\mijikaku-venv\Scripts\python.exe" -m pip --version
& "C:\作業場所\mijikaku-venv\Scripts\python.exe" -m pip install -r "C:\スキル配置先\mijikaku\scripts\requirements.txt"
```

準備の詳細は[Python Packaging User Guide](https://packaging.python.org/en/latest/guides/installing-using-pip-and-virtual-environments/)を参照する。

## 実行と出力

対象は`.md`ファイルまたはディレクトリで、ディレクトリ内は再帰的に検査する。対象と出力先は絶対パスで指定し、出力先には検査対象の外にある新しい一時ファイルを選ぶ。

[仮想環境のPythonを直接指定](https://docs.python.org/3/library/venv.html#how-venvs-work)して実行する。

sh系シェル：

```bash
"/作業場所/mijikaku-venv/bin/python" "/スキル配置先/mijikaku/scripts/detect.py" "/対象/設計書.md" --out "/一時作業場所/候補.json"
```

PowerShell：

```powershell
& "C:\作業場所\mijikaku-venv\Scripts\python.exe" "C:\スキル配置先\mijikaku\scripts\detect.py" "C:\対象\設計書.md" --out "C:\一時作業場所\候補.json"
```

出力はファイルごとに検出理由でまとめ、候補を1行ずつ並べる。ディレクトリ指定時のファイル名は、対象からの相対パスで示す。

ファイル内は、語や同文の繰り返し、否定・前置き・言い回しなどの表現、括弧書き、長さの順に並べる。同じ理由の候補は原文の行順に並ぶ。

```json
{
  "設計書.md": {
    "同文の繰り返し": [
      [62, "入力された明細を表示順に並べて画面に返す。", [17]]
    ],
    "手順の予告": [
      [48, "次の順で"]
    ],
    "長い段落": [
      [80, "一覧の取得条件と表示順を定める。…", 265, 4]
    ]
  }
}
```

各候補は `[行番号, 該当箇所]` で始まる。行番号はフロントマターを含む元のMarkdown上で、段落・表セル・箇条書き項目の開始行を1から数える。該当箇所には、一致した文字列を入れる。複数の文字列が一致した場合は配列になる。長さで検出した場合は本文の冒頭36字までを入れる。

- 同文の繰り返しには初出の行番号の配列を添える。
- 長さで検出した箇所には文字数、文数を順に添える。
- 同じ原文行に複数の候補があり、該当箇所が候補の一部の場合は、候補全体の表示文を末尾に添える。

候補に複数の検出理由があるときは、それぞれの理由に載る。実行時に表示する件数は、段落・表セル・箇条書き項目ごとに数える。修正は原文と前後を読んで判断する。

本文の段落・表セル・箇条書き項目を検査する。先頭のYAMLフロントマターは除く。
