# mijikaku

> 書かれていないことは行わない・起こらない。その前提で蛇足を削り、長文を整理する。

日本語Markdownの設計書の作成と推敲に使うAgent Pluginです。必要な情報を保ちながら、読む分量を減らします。

検出スクリプトが候補・理由・行番号を出し、AIが文脈を読んで修正を判断します。

## 導入

[Agent Plugins 1.0.0](https://agent-plugins.org/specification)形式です。`plugin.json`があるこのフォルダを、プラグインのルートとして導入します。

| ツール | 導入方法 |
|---|---|
| VS Code＋Copilot | [`Chat: Install Plugin from Source`](https://code.visualstudio.com/docs/agent-customization/agent-plugins#install-a-plugin-from-source)に、`https://github.com/ShokiTokunaga/mijikaku`またはこのフォルダを指定します。 |
| Copilot CLI | `copilot plugin install ShokiTokunaga/mijikaku`を実行します。[公式手順](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference#plugin-specification-for-install-command) |
| Codex | [カタログの設定手順](https://developers.openai.com/plugins/build/plugins#marketplace-metadata)に従い、マーケットプレイス経由で導入します。 |

直接使う場合は、`skills/mijikaku/`を一式保存し、[SKILL.md](skills/mijikaku/SKILL.md)をAIへ渡してください。

## Pythonと依存パッケージの準備

検出ツールにはPython 3.12以上と[依存パッケージ](skills/mijikaku/scripts/requirements.txt)が必要です。

AIが[実行手順](skills/mijikaku/references/detector.md#環境の確認と初回準備)に従い、使用環境に合う専用の仮想環境を準備・再利用します。

## 使用例

作成するとき：

> mijikakuを使い、要件.mdをもとに基本設計書を作成してください。

推敲するとき：

> mijikakuを使い、設計書.mdを推敲してください。

## ライセンス

[MIT License](LICENSE)
