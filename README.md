# GNSS 模块测试 Web App

运行在 AW3588-EC (CM3588-Plus, Ubuntu 24.04) 上，测试 Quectel EM05-CN 内置 GNSS。

- 访问地址: **http://10.11.0.105:8080/**
- 服务: `systemctl status gnss-test`（开机自启，目录 `/home/pi/gnss_test`）
- 依赖: `python3-flask`、`python3-serial`（已通过 apt 安装）

## 端口

| 端口 | 用途 |
|---|---|
| `/dev/ttyUSB1` | NMEA 输出 (usbnmea) |
| `/dev/ttyUSB3` | AT 命令 (`ttyUSB2` 被 ModemManager 独占) |

## 功能

- **实时监测**：定位状态、经纬度/海拔、卫星使用/可见数（按星座区分）、C/N0、DOP、UTC；星空图、C/N0 柱状图、定位散点（CEP50/CEP95）、卫星列表；GNSS 开/关、冷/温/热启动（带定位计时）；NMEA 录制。
- **自动测试**：冷/温/热启动 → 测 TTFF → 观测 N 秒 → 统计（卫星数、Top4 C/N0、HDOP、CEP50/95、定位保持率）→ 按标准自动判定 PASS/FAIL；历史结果可导出 CSV。
- **NMEA / AT**：原始 NMEA 实时滚动（可过滤/暂停）、AT 命令终端。
- **配置**：串口选择、`AT+QGPSCFG` 读写，以及一键启用 GLONASS/BeiDou/Galileo NMEA 输出。

## 开发 / 部署

```powershell
pip install paramiko
python deploy.py            # 上传并重启服务
```

## 备注

- `AT+QGPSDEL`: 0 = 冷启动, 1 = 热启动, 2 = 温启动（启动前会先执行 `AT+QGPSEND`）。
- 模块默认只输出 GPS 语句（`glonassnmeatype` / `beidounmeatype` / `galileonmeatype` 都是 0），需要在“配置”页一键启用。
- 测试需要接 GNSS 天线并放到室外或窗边。
