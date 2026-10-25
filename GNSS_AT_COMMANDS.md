# Quectel EM05-CN 开启全星座 GNSS 的 AT 指令说明

本文档说明如何在 **Quectel EM05-CN**（固件版本：`EM05CNFDR08A03M1G_ND`）上通过 AT 指令开启 **GPS + 北斗 (BeiDou) + GLONASS + Galileo + QZSS + SBAS** 全星座定位与 NMEA 语句输出，并解决“只输出 GPS”或“北斗出现几秒后自动消失”的问题。

---

## 1. 串口说明

在 AW3588-EC (Ubuntu 24.04) 上，EM05-CN 映射为 4 个 USB 串口：

| 端口 | 用途 | 备注 |
| :--- | :--- | :--- |
| `/dev/ttyUSB0` | DM / 诊断端口 | - |
| `/dev/ttyUSB1` | **NMEA 输出端口** (`usbnmea`) | 默认波特率 `115200` |
| `/dev/ttyUSB2` | AT 主端口 | 常被系统 `ModemManager` 服务占用 |
| `/dev/ttyUSB3` | **AT 备用端口（推荐使用）** | 默认波特率 `115200` |

---

## 2. 一键开启全星座的完整 AT 指令序列

按顺序向 `/dev/ttyUSB3` 发送以下指令即可开启全星座并固化到 NVRAM（掉电不丢失）：

```at
// 1. 先关闭正在运行的 GNSS 引擎（部分配置需在引擎关闭状态下修改才能生效）
AT+QGPSEND

// 2. 配置星座组合为全星座常开（GPS + BeiDou + GLONASS + Galileo + QZSS）
AT+QGPSCFG="gnssconfig",4

// 3. 关闭动态省电优化（DPO），防止定位稳定后基带自动关停北斗/Galileo通道
AT+QGPSCFG="dpoenable",0

// 4. 开启各星座的 NMEA 语句输出掩码
AT+QGPSCFG="gpsnmeatype",31       // GPS: GGA(1)+RMC(2)+GSV(4)+GSA(8)+VTG(16) = 31
AT+QGPSCFG="glonassnmeatype",7    // GLONASS: GSV(1)+GSA(2)+GNS(4) = 7
AT+QGPSCFG="galileonmeatype",1    // Galileo: GSV(1) = 1
AT+QGPSCFG="beidounmeatype",3     // BeiDou: GSA(1)+GSV(2) = 3

// 5. 配置 NMEA 输出端口为 USB NMEA 口（/dev/ttyUSB1）
AT+QGPSCFG="outport","usbnmea"

// 6. （可选）设置模块上电自动开启 GNSS
AT+QGPSCFG="autogps",1

// 7. 重新启动 GNSS 引擎使配置立即生效
AT+QGPS=1
```

> **提示**：如果模块是从出厂默认状态首次修改 `gnssconfig`，在极端情况下若 `AT+QGPSEND` -> `AT+QGPS=1` 后仍未输出多星座语句，可执行一次模块软重启：
> ```at
> AT+CFUN=1,1
> ```

---

## 3. 常见问题与关键参数解析

### 3.1 为什么默认只有 GPS 信号（只有 `$GP*` 语句）？
模块出厂默认配置中：
- `AT+QGPSCFG="glonassnmeatype"` 为 `0`
- `AT+QGPSCFG="beidounmeatype"` 为 `0`
- `AT+QGPSCFG="galileonmeatype"` 为 `0`

即使底层搜到了其他星座的卫星，串口也不会输出对应的 `$GLGSV`、`$GAGSV`、`$PQGSV` 语句。必须将上述三项分别设为 `7`、`3`、`1`。

### 3.2 为什么开启后北斗（BeiDou）和 Galileo 出现几秒又消失？
有两个原因：
1. **`gnssconfig` 取值差异**：
   在 EM05-CN 该版本固件中，`gnssconfig=1` 稳态下仅保持 GPS + GLONASS（仅在启动前几秒短暂输出北斗缓存）。实测各取值在稳态（>30s）下的表现如下：

   | `gnssconfig` 值 | 稳态保持的星座 |
   | :---: | :--- |
   | `0` | 仅 GPS |
   | `1` | GPS + GLONASS + QZSS（北斗/Galileo 几秒后关闭） |
   | `3` | GPS + BeiDou + GLONASS + Galileo + QZSS |
   | **`4`（推荐）** | **GPS + BeiDou + GLONASS + Galileo + QZSS（全星座常开）** |
   | `5` | GPS + BeiDou + GLONASS + Galileo + QZSS |
   | `6` | GPS + BeiDou + GLONASS + Galileo + QZSS |
   | `7` | GPS + BeiDou + GLONASS + Galileo + QZSS |

2. **动态省电模式 (`dpoenable`) 干扰**：
   出厂默认 `AT+QGPSCFG="dpoenable",2`（启用动态省电）。当 GPS + GLONASS 信号良好（如 HDOP ≤ 0.7）时，基带为了省电会主动关闭北斗和 Galileo 射频通道。通过 `AT+QGPSCFG="dpoenable",0` 关闭省电后，所有星座将持续保持追踪。

---

## 4. NMEA 语句与星座对应关系（解析参考）

开启全星座后，`/dev/ttyUSB1` 每秒输出的 NMEA 0183 语句头及含义如下：

| 语句示例 | 所属星座 / 含义 | 关键字段说明 |
| :--- | :--- | :--- |
| `$GNGNS,...` | **多星座联合定位主语句**（`glonassnmeatype` bit2=4 开启） | 第 6 字段如 `AAA` 分别表示 GPS/GLONASS/Galileo 的定位模式；第 7 字段为联合定位卫星数；经纬度精确到 `0.000001'`（约 **1.8 mm** 分辨率） |
| `$GPGGA,...` | **定位解算输出**（与 `$GNGNS` 坐标相同） | 第 6 字段为定位质量（`1`=单点，`2`=DGPS）；第 7 字段仅统计 GPS 卫星数（最高 12） |
| `$GPGSV,...` | **GPS + SBAS** 可见卫星 | PRN `1~32` 为 GPS；PRN `33~64` 为 SBAS |
| `$GLGSV,...` | **GLONASS** 可见卫星 | PRN `65~96` |
| `$GAGSV,...` | **Galileo** 可见卫星 | PRN `1~36` |
| `$PQGSV,...,0,4*xx` | **BeiDou (北斗)** 可见卫星 | 末尾倒数第 1 个字段 `system_id = 4` 代表北斗 |
| `$PQGSV,...,0,5*xx` | **QZSS** 可见卫星 | 末尾倒数第 1 个字段 `system_id = 5` 代表 QZSS |
| `$GNGSA,...,1*xx` | **GPS** 参与定位卫星 | 末尾 `system_id = 1` |
| `$GNGSA,...,2*xx` | **GLONASS** 参与定位卫星 | 末尾 `system_id = 2` |
| `$GNGSA,...,3*xx` | **Galileo** 参与定位卫星 | 末尾 `system_id = 3` |
| `$PQGSA,...,4*xx` | **BeiDou (北斗)** 参与定位卫星 | 末尾 `system_id = 4` |
| `$PQGSA,...,5*xx` | **QZSS** 参与定位卫星 | 末尾 `system_id = 5` |

---

## 5. 常用查询与测试指令汇总

```at
// 查询当前所有 GNSS 配置项
AT+QGPSCFG="gnssconfig"
AT+QGPSCFG="dpoenable"
AT+QGPSCFG="gpsnmeatype"
AT+QGPSCFG="glonassnmeatype"
AT+QGPSCFG="galileonmeatype"
AT+QGPSCFG="beidounmeatype"
AT+QGPSCFG="autogps"

// 查询 GNSS 引擎运行状态（1=运行中，0=已停止）
AT+QGPS?

// 直接通过 AT 口查询当前定位坐标
AT+QGPSLOC=2

// 冷 / 温 / 热启动测试（需先执行 AT+QGPSEND）
AT+QGPSDEL=0    // 删除全部辅助数据（冷启动准备）
AT+QGPSDEL=1    // 不删除数据（热启动准备）
AT+QGPSDEL=2    // 删除部分星历（温启动准备）
```
