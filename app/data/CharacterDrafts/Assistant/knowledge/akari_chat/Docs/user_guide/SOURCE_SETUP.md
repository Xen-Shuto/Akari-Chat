# ソース構成から使う場合

2026年10月31日版。

[ユーザーガイド](USER_GUIDE.md) / [はじめに](GETTING_STARTED.md)

Akari-Chat_Cleanは、Git管理や配布前の確認に使うソース一式です。通常の利用フォルダから、利用者データ・モデル・実行バイナリを除いて構成しています。

## 含まれるもの

- app.py / launch.py、litechat、tools、Web画面とMarkdown用の同梱ライブラリ。
- Start.bat、README、Docs、Git用の除外/改行設定。
- CharacterDraftsの標準キャラ草案・表情画像・同じ版の案内資料。
- llama.cppのREADMEとLICENSE、Modelsの.gitkeep。

CharacterDraftsは利用中のキャラとは別の草案です。初回起動へ自動導入する機能ではありません。使用する場合は、内容を確認してキャラ取込から取り込めます。

## 別途用意するもの

1. CPython 3.10.11（64bit）を採用環境として準備します。app/runtimeがなければStart.batはPATHのpythonを使用します。
2. ローカルLLMを使う場合はllama-server.exeと、同じビルドの必要なDLLをllama.cppに配置します。
3. 会話用GGUF・必要なmmproj/MTP・検索用GGUFをModels等へ置きます。
4. ドライバーなどの実行条件を整え、Start.batを起動します。
5. 共通設定を保存し、キャラを作成/取り込んで動作を確認します。

公開PDF取得などの任意機能は、対応する追加依存関係も必要です。ソースに機能があることと、このフォルダだけで全実行環境が揃うことは別です。

## 含めないもの

app/data、app/characters、app/runtime、モデル、推論EXE/DLL、Exports、キャッシュ、秘密鍵・認証情報、開発用の試験結果や既存Git履歴は含めません。起動用のDEBUG_MODEとINIT_SETTINGは無効にした構成です。

実行すると新しいapp/dataとapp/charactersが作られます。実行前のソース一式と、利用後のフォルダを取り違えないでください。

## 公開・配布する前に

同梱するキャラ・資料・画像・実行ファイル・モデルの配布条件を確認してください。Markdown用ライブラリのライセンスはapp/web/vendorにあります。推論エンジンやモデルの条件、別PCでの実動作確認は別途必要です。この構成の作成によって、アプリ全体やモデルへ新しいライセンスを付与することはありません。
