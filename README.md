# CardioAgent 2.4.1 會診準備中心

新增 CAC 與本機 LLM 同次驗收、模型回應身分核對、可登錄的 NMS 閾值，以及來源變更後的續跑／覆核阻擋。

- [公開操作](https://poemcheng.github.io/cardioagent-workstation/?view=workspace&revision=2.4.1)
- 本機：`http://127.0.0.1:8765/workspace`
- [操作與版本範圍](docs/RELEASE_2.4.1_zh-TW.md)
- [Windows 軟體安裝包](https://github.com/poemcheng/cardioagent-workstation/releases/latest)

以下保留既有工作站及研究功能說明；歷史版本紀錄不代表本版 AMD 或臨床驗收。

# CardioAgent 心臟影像研究工作站

**公開操作網址：[開啟工作站](https://poemcheng.github.io/cardioagent-workstation/)**

**AMD AI PC 2.3.1 本機展示：**新增會診交談擷取、來源回查與交接缺漏檢查；已下載 MTS-Dialog 1,701 筆及建立 12 個中文合成流程案例。Windows 解壓後啟動 `start_aipc_windows.cmd`，操作與模型下載見 [AI PC 操作與驗收指南](docs/AMD_AIPC_GUIDE.md)。此功能在本機執行，公開網頁版維持原有影像流程。LLM 權重及 NPU 驅動需在目標機安裝；目前未宣稱 NPU 實測完成。

**換機安裝與驗收：**[下載完整 Windows 套件](https://github.com/poemcheng/cardioagent-workstation/releases/latest) → [2.3.1 安裝與實機驗收](docs/AMD_AIPC_QUICKSTART_2.3.1.md)。安裝 Python 3.11 x64 後執行 `setup_windows.ps1 -Offline -WithVision`，再執行 `accept_aipc_windows.cmd` 產生當次 HTML／JSON。完整 Release 附件有 wheels；Source code.zip 沒有。詳見 [版本修正](docs/RELEASE_2.3.1_zh-TW.md)。

**2026-11-03 競賽資源核對修正：**預設模型改為 `Phi-4-mini-instruct-GGUF`（Microsoft Phi 基礎模型，MIT；GGUF 來自 Unsloth），屬 CPU／GPU 路徑。若兼報開源 AI 模型應用組，須遵守核心模型來源限制，不能直接沿用前版 Qwen 預設。Joint Lab 申請表目前已停止收件；八項資源用途、Hybrid 版本限制及尚待驗證項目見 [競賽資源核對表](docs/AMD_COMPETITION_RESOURCE_AUDIT.md)。先前下載包亦可用 `scripts\setup_aipc.ps1 -PullModel -Model Phi-4-mini-instruct-GGUF` 明確指定模型；先前 PDF 的 Qwen／FLM 預設說明以本次線上指南修正為準。

沿用Flask／SQLite／NumPy、Lemonade及ONNX架構，提供來源核對、影像辨識或量測、分割、參考比對、3D表面、證據圖、人工討論與報告流程。公開版以WebAssembly在訪客瀏覽器執行原有Python程式，各分頁資料獨立，無共用病人資料庫。

| 執行模式 | 資料與能力 |
|---|---|
| 公開網頁 | MRI／合成體模、原圖與遮罩、閾值及提供標籤、比對、3D、人工覆核、報告與匯出；本分頁暫存 |
| AMD AI PC 本機 | 原有Python環境、帳號／角色、持續保存、可設定本機Lemonade及ONNX；仍需目標機驗收 |

首次開啟公開網址會下載分析環境。操作結果請下載JSON保存，重新整理或關閉分頁會清除。公開版未連接LLM／臨床模型／AMD NPU，術前討論不自動選擇手術或開立醫囑。

Windows使用Python 3.11 x64及Python Launcher，完整下載解壓後雙擊start_demo_windows.cmd。GitHub來源首次從PyPI安裝套件，影像從docs/cardio-runtime.zip經SHA256核對後還原，不需另外下載模型。

- [公開部署與操作](docs/PUBLIC_DEPLOYMENT_zh-TW.md)
- [完整操作手冊](docs/INTEGRATED_USER_MANUAL_zh-TW.md)
- [AMD本機設定與功能](README_zh-TW.md)
- [方法及來源](docs/PIPELINE_METHODS.md)
- [驗證範圍](docs/VALIDATION.md)

MRI提供標籤重建有75,846體素、2,412三角面，間距未驗證時使用voxel；合成體模閾值120有28,736體素、89,800合成mm³、Dice1.0。這是研究資料與流程的一致性結果，不是臨床模型效能。
