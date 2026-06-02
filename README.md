# pcinfo

WindowsPCのCPU・メモリ使用率をブラウザで見られるソフトです。

- **CPU使用率**: 縦の棒グラフ（直近60秒分）を1秒ごとに更新
- **メモリ使用率**: 円（ドーナツ）グラフを1秒ごとに更新

Python標準ライブラリ + `psutil` のみで動作し、追加のWebフレームワークは不要です。

## 必要なもの

- Python 3.8 以上（Windows側）
- `psutil`

```powershell
pip install psutil
```

## 使い方

> 正確なWindowsの情報を取得するため、**Windows側のPython**で実行してください。

```powershell
python server.py
```

起動したらブラウザで以下を開きます:

```
http://localhost:8000
```

停止するには実行中のコンソールで `Ctrl + C` を押します。

## 構成

| ファイル | 役割 |
| --- | --- |
| `server.py` | 標準ライブラリのHTTPサーバー。`/api/stats` でJSONを返す |
| `index.html` | Chart.js を使った画面（CPU=棒グラフ / メモリ=円グラフ） |

## 補足

- ポートを変えたい場合は `server.py` の `PORT = 8000` を編集してください。
- 同じLAN内の別端末から見るには、`http://<PCのIPアドレス>:8000` を開きます
  （Windowsファイアウォールで許可が必要な場合があります）。
