# AB MAP NOTAM Feed

AB MAP iOSアプリ向けに、日本のSWIMからNOTAMを取得して読み取り専用JSONを生成します。

- GitHub Actionsが3時間ごとに実行
- `SWIM_USER_ID` / `SWIM_PASSWORD` はRepository Secretsのみで管理
- GitHub Pagesから `public/v1/notams.json` を配信
- 正規の航空情報ではありません

## 初期設定

1. Repository Settings > Secrets and variables > Actionsに`SWIM_USER_ID`と`SWIM_PASSWORD`を登録します。
2. Repository Settings > Pages > Sourceで`GitHub Actions`を選択します。
3. Actionsから`Refresh NOTAM feed`を手動実行します。

