# AMD 競賽資源核對與 CardioAgent 採用方案

核對日期：2026-11-03，臺灣時間。核對範圍：主辦單位規則、連結實際導向、官方 GitHub 程式與文件、目前 CardioAgent 2.2 程式。文件可讀、授權可核對、服務實際可用及裝置執行成功分開記錄。

## 競賽條件

AMD 組官方說明要求決賽實際使用 AMD 相關資源，可包含 AMD-ITRI Joint Lab、所提供的雲端 LLM、AMD GPU 或 AMD AI PC。該頁列出工具作為開發資源，未要求八項全部安裝，也未明定必須同時使用 NPU、GPU 與雲端。以實際 AMD AI PC 執行本機模型是可規劃的實作路徑；最後仍須提供現場執行證據並由評審認定。

此組以自主規劃與完成任務的 Agentic AI 為主題。現有軟體有工具編排、結構規則、摘錄核對與人工覆核，但受限制的固定工具次序不等於開放式自主規劃；展示應呈現目標、工具調用、驗證、缺漏後的處理分支與完成紀錄。

若另報「開源 AI 模型應用組」，官方禁止中國機構、企業或研究單位開發或維護的開源 AI 模型作為核心，並要求揭露模型名稱、版本、來源及授權。此項是該組的條件，不能因 AMD 範例有 Qwen，就推定它符合另一組。前版預設 Qwen 在兼報該組時需要更換。

另依官方一般類別規定，兩組都屬「指定專題類」，同一隊每一類別最多報名一組；不能自行推定同隊可同時報 AMD 與 AI-Open Source，應依實際報名紀錄及主辦單位組別調配核對。

AMD 組公告決賽為 2026-11-07；評分含技術性 20%、創新性 30%、市場性 40%、展示表達 10%。全員註冊 AMD AI Developer Program 是另列的酌予加分條件，最高 5%；不是安裝八項軟體的替代條件。

官方來源：

- https://innoserveawards.tca.org.tw/advertise26_AMD.htm
- https://innoserve.tca.org.tw/Rules/Detail?category=College

## 八項資源逐一核對

| 資源 | 核對結果 | CardioAgent 用法與條件 |
|---|---|---|
| AMD-ITRI Joint Lab 申請 | 已開啟官方連結；表單標題為 2026 AMD 組，頁面顯示已不接受回覆，實際網址為 closedform | 不把新申請當展示前置條件。既有帳號或重新開放申請須向資源管理者確認，不能據表單標題宣稱已獲算力。 |
| AMD LLM 操作範例 / 短連結 | 已追到 `AMD-AI-Agent-Online-Hackthon/Documents/workshop.md`，內容為 vLLM GPT-OSS-120B 的兩個遠端 OpenAI 相容 API | 可作雲端合成資料實驗的參考。兩端點當前存活與配額未驗證；範例中的 `model_name` 沒有先定義，需先取得實際 `/models` 清單。此為遠端 HTTP 服務，不是 PN54 的本機 NPU。 |
| AUP Learning Cloud | 官方 repo 與 CV、DL、LLM、PhySim 實驗存在；LLM 目錄有 14 個教學 notebook | 可學習或用已獲權限的 AMD GPU 雲端做訓練/評估。自行架完整平台的目前要求含 Ubuntu 24.04.4、至少 32 GB RAM（建議 64 GB）與 Docker，不適合直接套用到 Windows 16 GB 的展示機。 |
| Adrenalin AI Bundle | AMD 2026-01-21 官方文章列 PyTorch、ComfyUI、Ollama、LM Studio；列含 Ryzen AI 300/400/Max 與 Radeon RX 7700 起的硬體類別 | 可作環境安裝工具。各軟體的實際 GPU 支援仍要各別核對；安裝 Bundle 不代表 NPU 已執行，也不是 CardioAgent 的必要依賴。 |
| Lemonade SDK 文件 | 指向現行 Lemonade 同一專案的 guide、API、embeddable 文件 | 沿用 REST 介接，無需另建第二套獨立 LLM 平台。 |
| Lemonade Server | 現行文件提供 OpenAI 相容 `/v1/models`、`/v1/chat/completions`；部分版本/封裝另用 `/api/v1` | 作為本機主路徑。現有程式僅允許 loopback，禁止 redirect、未下載模型及 cloud_provider。實際模型 ID、recipe、usage 和失敗結果保留。 |
| Ryzen AI Hybrid / OGA INT4 | 官方程式與 OGA 文件可讀，支援 Hybrid 及 NPU-only；目前官方文件為 Ryzen AI 1.9 | 作 NPU+iGPU 或 NPU 實機驗證。模型、驅動、OGA 與 runtime 必須匹配；官方 1.9 明示舊版 NPU 模型不相容，不能任意混裝 1.7/1.8 模型與 1.9 NPU runtime。 |
| YOLO / Ryzen AI Object Detection | 目前含 yolov8m、yolov8s-worldv2；yolov8m 流程含 ONNX、Quark 量化、Vitis AI EP、CPU/NPU benchmark | 可驗證 NPU 視覺管線。官方 COCO 模型不是心臟鈣化或狹窄模型；醫療用途須另備相符資料、權重、標註與測試，不能把物件偵測當成 3D 分割或手術規劃。 |

兩個短連結及實際來源：

- Joint Lab：https://forms.gle/FtzoVve9wCpCVfDD9
- 表單實際頁面：https://docs.google.com/forms/d/e/1FAIpQLSfNv1PccvHMh-8e6becPCo9sVZjYGnr44NzYe-ehQI-PmqBYA/closedform
- LLM 範例短連結：https://pse.is/92fn8q
- LLM 範例實際文件：https://github.com/AMD-AI-Agent-Online-Hackthon/Documents/blob/main/workshop.md

## 模型選擇修正

| 配置 | 基礎與轉換來源 | 現階段定位 |
|---|---|---|
| `Phi-4-mini-instruct-GGUF` | Microsoft Phi-4-mini，GGUF 由 Unsloth 發布；兩份模型卡標 MIT。Lemonade catalog 為 Q4_K_M，下載大小約 2.49 GB | 競賽啟動預設，先驗證地端文字流程。CPU/iGPU 的實際執行依 backend 與 log 確認，不能稱 NPU。下載大小不等於總記憶體。 |
| `Phi-4-mini-instruct-Hybrid` | Microsoft 基礎模型；Lemonade catalog 對應 AMD 的 Ryzen AI 1.7 hybrid checkpoint，約 5.47 GB | NPU+iGPU 候選。必須使用該 backend 支援的匹配 runtime；不能直接當作 Ryzen AI 1.9 NPU 模型。16 GB 主機需實測共享記憶體與峰值 RAM。 |
| 官方 1.9 NPU 模型集中的 Phi 變體 | 依 AMD 模型集、模型卡及實機支援選取 | 後續 NPU-only 候選，尚未下載或執行。 |

上述是部署候選，沒有保證繁中醫療任務品質或最終參賽資格。來源與授權需在作品文件揭露，並保留實際下載的 revision、檔案雜湊、量化版本及原模型/轉換模型授權。不能把其他模型的測試結果套用到 Phi。

已核對的 Lemonade catalog 路徑為 `src/cpp/resources/server_models.json`，核對時 Git blob SHA：`bd5cc5788fe8bac1c366ab4320b680fcaf2340e2`。上游 main 會變動，下載時仍需以目標機的模型清單為準。

既有 2.2 下載包可明確指定模型，無須沿用它的 Qwen 預設：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_aipc.ps1 -PullModel -Model Phi-4-mini-instruct-GGUF
```

先啟動 Lemonade，再執行上述命令。新 GitHub 原始碼已更改預設；舊 PDF 的 FLM/Qwen 預設說明以線上指南與本核對表為準。

## 決賽要呈現的醫療流程

目標是「準備一份可回查的會診資料包」：核對來源 → 摘錄原文 → 列出交接缺漏 → 依缺漏與急性旗標分流 → 人員覆核 → 匯出證據。相應痛點是減少重複整理、資訊遺漏與查找來源的負擔；節省時間與重大缺漏率尚需人員任務測試。

文字與 MRI 沒有病人配對時分開展示。MRI 的參考標註表面不是新模型的預測，也不能當作手術還原。LLM 不替人決定手術或開立醫囑。

展示證據分成三層：

1. 功能：相同輸入的原文、摘錄、缺漏、工具紀錄與人工覆核。
2. AMD 使用：主機型號、runtime/backend、模型 ID、裝置選擇與同時間執行紀錄；NPU 利用率只作佐證。
3. 效益：人工與輔助流程含修正的總耗時、重大遺漏、來源查證時間與可用性；目前沒有宣稱臨床改善。

公開 GitHub Pages 在瀏覽器執行原有 Python/WASM 影像流程，未連地端 Lemonade，不能拿來當 NPU 或雲端 AMD LLM 已完成驗證的證據。

## 官方技術來源

- https://github.com/AMDResearch/aup-learning-cloud
- https://www.amd.com/en/blogs/2026/amd-software-adrenalin-edition-ai-bundle-ai-made-si.html
- https://github.com/lemonade-sdk/lemonade/blob/main/docs/README.md
- https://lemonade-server.ai/docs/api/openai/
- https://github.com/lemonade-sdk/lemonade/blob/main/src/cpp/resources/server_models.json
- https://ryzenai.docs.amd.com/en/latest/hybrid_oga.html
- https://github.com/amd/RyzenAI-SW/tree/main/LLM-examples
- https://github.com/amd/RyzenAI-SW/tree/main/CNN-examples/object_detection
- https://huggingface.co/microsoft/Phi-4-mini-instruct
- https://huggingface.co/unsloth/Phi-4-mini-instruct-GGUF
