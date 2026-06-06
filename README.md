# pcinfo

WindowsPCの **CPU・メモリ・ディスク・GPU** の使用状況をブラウザで見られるソフトです。
Tailscale（VPN）経由で、外出先など別ネットワークの端末からもリモートで閲覧できます。

- **CPU使用率**: 縦の棒グラフ（直近60秒分）＋ コア数・周波数
- **メモリ使用率**: 円（ドーナツ）グラフ＋ 使用量/全体/空き
- **ディスク**: アクティブ率(%)の折れ線＋読み書き速度(MB/s)、各ドライブ(C:/D:…)の容量使用率
- **GPU使用率**: 使用率(%)の折れ線＋VRAM使用量＋温度（NVIDIA / `nvidia-smi`）

すべて1秒ごとに自動更新。Python標準ライブラリ + `psutil` のみで動作し、追加のWebフレームワークは不要です。

## 必要なもの

- Python 3.8 以上（Windows側）
- `psutil`
- GPU表示を使う場合: NVIDIA GPU と `nvidia-smi`（NVIDIAドライバに同梱。非NVIDIA環境では「GPU情報なし」と表示され、他の指標は通常どおり動作します）

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

## リモート（Tailscale）で見る

別ネットワークの端末（スマホ・別PC）から安全に閲覧するには [Tailscale](https://tailscale.com/) を使います。ポート開放やインターネットへの直接公開は不要で、通信はTailscaleが暗号化・認証します。

1. **ホストPC** と **閲覧端末** の両方に Tailscale をインストールし、**同じアカウント（tailnet）** でサインインする。
2. ホストPCで `python server.py` を実行する。
3. ホストPCの Tailscale IP（例 `100.x.y.z`）を確認する（`tailscale ip -4`、またはTailscaleアプリ／管理コンソール）。
4. 閲覧端末のブラウザで以下を開く:

   ```
   http://<ホストのTailscale IP>:8000
   ```

   MagicDNS を有効にしている場合は `http://<マシン名>:8000` でもアクセスできます。
5. つながらない場合は、Windowsファイアウォールで `python` の受信（TCP 8000番）を許可してください。Tailscale経由のトラフィックはプライベートネットワーク扱いです。

> 補足: Tailnet内は実質プライベートなので本ソフトに認証は付けていません。より広い範囲に公開する場合は、簡易的なBasic認証やリバースプロキシでの保護を別途検討してください。

## 同じLAN内で見る

同じWi-Fi／LAN内の別端末からは、ホストPCのローカルIPで直接見られます:

```
http://<PCのローカルIPアドレス>:8000
```

（Windowsファイアウォールで許可が必要な場合があります。）

## 構成

| ファイル | 役割 |
| --- | --- |
| `server.py` | 標準ライブラリのHTTPサーバー。`/api/stats` で CPU/メモリ/ディスク/GPU のJSONを返す |
| `index.html` | Chart.js を使ったダッシュボード画面（4パネル） |
| `requirements.txt` | 依存（`psutil`） |

## 補足・カスタマイズ

- ポートを変えたい場合は `server.py` の `PORT = 8000` を編集してください。
- ディスクの「アクティブ率(%)」は `psutil` の read/write 時間の差分による近似値です（タスクマネージャーの値とは多少ずれます）。
- GPUの値は `nvidia-smi` を約0.8秒キャッシュして取得しています。複数GPUにも対応しています。
