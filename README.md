# GitHub 天气推送

一个无需自建服务器的天气提醒工具。GitHub Actions 每 10 分钟查询一次 Open-Meteo；当选中的天气类型从“未出现”变为“出现”时发送一次提醒，天气持续期间不会重复推送。

支持的天气类型：

| 配置值 | 天气类型 | 说明 |
|---|---|---|
| `rain` | 下雨 | 毛毛雨、雨、冻雨、阵雨和伴雨雷暴 |
| `snow` | 下雪 | 雪、米雪和阵雪 |
| `thunderstorm` | 雷暴 | 含伴冰雹雷暴 |
| `fog` | 大雾 | 雾和雾凇 |
| `wind` | 大风 | 默认风速 ≥ 10.8 m/s，可改阈值 |
| `hot` | 高温 | 默认气温 ≥ 35°C，可改阈值 |
| `cold` | 低温 | 默认气温 ≤ 0°C，可改阈值 |
| `cloudy` | 多云/阴天 | WMO 代码 1–3 |
| `clear` | 晴天 | WMO 代码 0 |

## 一、部署到 GitHub

1. 在 GitHub 新建仓库，把本项目推送到仓库的默认分支。
2. 打开仓库的 **Settings → Actions → General → Workflow permissions**，选择 **Read and write permissions** 并保存。
3. 打开 **Actions → 配置天气类型 → Run workflow**，用复选框选择要提醒的天气并运行。
4. 打开 **Actions → 天气监测与推送 → Run workflow**，保持“只测试”开启，先跑一次。
5. 测试成功后再次运行并关闭“只测试”，或等待定时任务自动执行。

> GitHub 的定时任务可能比设定时间晚几分钟，适合生活提醒，不适合作为灾害预警系统。

当前配置每 10 分钟运行一次。标准 GitHub 托管运行器用于公开仓库时免费；私有仓库会消耗账户的 Actions 分钟额度。如希望节省额度，可把 `.github/workflows/weather-watch.yml` 中的 `*/10` 改为 `*/30`。

## 二、选择位置和天气类型

最直观的方式是在 **Actions → 配置天气类型 → Run workflow** 中勾选。运行后，工作流会自动把选项保存到 [`config.json`](config.json)。也可以直接编辑该文件：

```json
{
  "location": {
    "name": "上海",
    "latitude": 31.2304,
    "longitude": 121.4737,
    "timezone": "Asia/Shanghai"
  },
  "enabled_weather": ["rain", "thunderstorm"]
}
```

经纬度可在地图上查询。若仓库是公开的、不想公开精确位置，可在 **Settings → Secrets and variables → Actions → Variables** 添加以下变量，它们会覆盖文件中的值：

- `LOCATION_NAME`：显示名称，例如 `杭州`
- `LATITUDE`：纬度，例如 `30.2741`
- `LONGITUDE`：经度，例如 `120.1551`
- `TIMEZONE`：时区，例如 `Asia/Shanghai`
- `WEATHER_TYPES`：逗号分隔的天气选项，例如 `rain,snow,thunderstorm`
- `NOTIFICATION_CHANNELS`：逗号分隔的通知渠道，例如 `pushplus,bark`

## 三、选择通知方式

在 `config.json` 的 `notifications` 数组中选择渠道，也可使用上面的 `NOTIFICATION_CHANNELS` 仓库变量覆盖。可以同时选多个渠道。

### GitHub 通知（零额外配置）

```json
"notifications": ["github"]
```

开始下雨时会在仓库新建一个标题含 `[weather-alert]` 的 Issue。安装 GitHub 手机 App，并把该仓库的 Watch 设置为包含 Issues，即可收到手机提醒。

### PushPlus 微信推送

```json
"notifications": ["pushplus"]
```

在 PushPlus 获取 token，然后在仓库 **Settings → Secrets and variables → Actions → Secrets** 新建 `PUSHPLUS_TOKEN`。

### Server酱微信推送

```json
"notifications": ["serverchan"]
```

在仓库 Secrets 新建 `SERVERCHAN_SENDKEY`。

### Bark（iPhone）

```json
"notifications": ["bark"]
```

安装 Bark，在仓库 Secrets 新建 `BARK_KEY`。如果使用自建 Bark 服务，再添加 `BARK_URL`。

### Telegram

```json
"notifications": ["telegram"]
```

在仓库 Secrets 新建 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID`。

### 自定义 Webhook

```json
"notifications": ["webhook"]
```

在仓库 Secrets 新建 `WEBHOOK_URL`。程序会 POST JSON，字段包括 `title`、`body`、`weather_types`、`weather_code` 和 `observed_at`。

## 四、本地测试

只查询并打印，不发送通知：

```bash
python src/weather_push.py --dry-run
```

运行单元测试：

```bash
python -m unittest discover -s tests -v
```

## 工作原理与限制

- 天气数据来自 Open-Meteo，无需 API Key。
- `.weather-state.json` 只在天气命中状态改变时由 GitHub Actions 提交，避免连续重复通知。
- 第一次运行时如果正在下雨，会立即推送一次。
- 如果所有通知渠道均失败，旧状态不会更新，下次检查会重试。
- Actions 免费额度和定时任务策略以 GitHub 当前规则为准；公共仓库长期无活动时，定时任务可能被暂停。
