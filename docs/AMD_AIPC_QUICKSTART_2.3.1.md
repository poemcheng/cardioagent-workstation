# CardioAgent 2.3.1：AMD AI PC 安裝、操作與實機驗收

2026-11-03。延續現有 Flask／SQLite／NumPy、Lemonade、ONNX 與 WebGL 架構。本版新增乾淨安裝包、一鍵驗收及來源可查的 HTML／JSON 報告；影像辨識契約和 3D 演算法沿用 2.3.0。

## 1. 下載與安裝

1. 從 [公開 Releases](https://github.com/poemcheng/cardioagent-workstation/releases/latest) 下載 `CardioAgent_AMD_AIPC_v2.3.1-<commit>_Windows_Py311.zip` 和 `.zip.sha256`。
2. 先安裝 **Python 3.11 x64（含 Python Launcher）**，完整解壓到可寫入的資料夾。此包的 wheels 固定供 CPython 3.11 x64；Python 3.12、ARM64 或 32 位元不可直接使用。
3. 在 PowerShell 核對下載 SHA256，再執行離線套件安裝。請將下面的 `<實際檔名>` 換成下載檔名；不必修改任何硬體設定。

```powershell
Get-FileHash .\<實際檔名>.zip -Algorithm SHA256
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 -Offline -WithVision
```

SHA256 應與同名 `.zip.sha256` 文字檔一致。封裝版內 `RELEASE_INFO.json` 提供來源 commit、套件版本、wheels 指紋與資料 hash；`PACKAGE_MANIFEST.json` 是本次建包生成的逐檔清單，安裝前會先核對。內容更動會造成核對失敗，請以全新解壓資料夾安裝。

完整 Release 附有 **1,701 筆 MTS-Dialog、12 個中文合成流程案例、MRI 研究標籤與影像還原套件、Windows Python wheels**。不含 Python 安裝器、AMD 驅動、Lemonade 安裝器、LLM 或臨床影像模型權重。已準備以上外部條件後，先驗證網路關閉時的冷啟動，再宣稱現場可離線展示。

GitHub 的 **Source code.zip** 是原始碼下載，未附 Windows wheelhouse；首次安裝需連線至 PyPI，與本頁的完整 Release 附件不同。

## 2. 先確認基線與 3D

雙擊 `accept_aipc_windows.cmd`，或執行：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_demo_assets.py
.\.venv\Scripts\python.exe scripts\prepare_aipc_data.py
.\.venv\Scripts\python.exe scripts\accept_aipc.py
```

輸出每次使用新的 `evidence/target_runs/<UTC時間>-<識別碼>/`；程式會列印報告的絕對路徑。開啟該資料夾的 **index.html**，並保留 acceptance.json、baseline.json、images.json、preflight.json、software.json 及 SHA256SUMS.txt。

| 項目 | 本次實際執行 | 判讀 |
|---|---|---|
| 資料 | 對話集 SHA256、MRI 來源與 Python 套件檢查 | 是資料／環境就緒檢查 |
| 文字 | 固定 acceptance 切分的 400 筆公開對話＋12 筆合成資料 | 顯示段落分類、逐行引文核對、缺漏規則；不是診斷效能 |
| 合成體模 | 32×96×96、閾值 120、已知參考 | 28,736 體素、Dice 1.0 僅限設計體模的一致性 |
| MRI 聯集 | 來源研究標籤直接重建 | 75,846 體素；不列模型分割準確率 |
| MRI 類別 2 | 薄標籤、顯示預算與遮罩／來源網格匯出 | 25,573 體素；顯示三角面須不超過 180,000 |
| 人工關卡 | 帳號與 CSRF、未覆核時禁止取得報告事實草稿 | 不自動簽核或作個別治療決策 |

影像驗收使用隔離暫存資料夾與 **Flask test client**，包括保存及匯出，沒有量測瀏覽器繪圖、TCP 服務、人工操作或臨床時間。完成後暫存帳號與資料會移除，日常工作站帳號及資料不受影響。報告仍需另外做下一節的人員操作確認。

MRI 的 voxel 與顯示用採樣比例分開保存，mm³ 為 null；不可用顯示平滑後的表面量測人體尺寸。

## 3. 實際操作展示

- **文字與會診準備：**雙擊 `start_aipc_windows.cmd`，首次設定自己的管理者帳號，開啟 `http://127.0.0.1:8765/aipc`。先選中文合成案例「完整交接」與「缺少藥單」，比較來源行、缺漏與引文；急性案例優先顯示人工處理，不等待模型。
- **影像與 3D：**雙擊 `start_demo_windows.cmd`，開啟 `http://127.0.0.1:8765/analysis`。同一時間只啟動一個服務；可在已啟動的服務直接切換 `/aipc` 或 `/analysis`。
- MRI 分割來源選「提供的研究標籤 · 非 AI 推論」，先選聯集，再選類別 2。檢查原圖、遮罩與疊圖；在 3D 切換顯示表面／來源網格及採樣比例。
- 完成討論欄位與人工分析覆核後，可帶入未簽核報告；報告仍須人工編輯、保存及獨立簽核。匯出 JSON、OBJ／STL，保存來源、單位和版本。
- 工作站資料保存在 `integrated_demo_data/`。停止服務後再備份整個資料夾；不要只備份單一 SQLite 檔案。

公開展示網址為 <https://poemcheng.github.io/cardioagent-workstation/>。它在各訪客的瀏覽器分頁執行，重新整理或關閉分頁會清除暫存；它不連接這台 PC 的 Lemonade／NPU，也不能替代本機 AMD 展示。

## 4. AMD 工具與模型的實際接線

先依目前官方支援矩陣安裝適合 CPU、Windows 及 Ryzen AI 型號的 Lemonade 與後端，預先下載模型並啟動服務。

- [Lemonade 官方文件](https://lemonade-server.ai/docs/)
- [Lemonade 官方模型與 CLI 文件](https://github.com/lemonade-sdk/lemonade/tree/main/docs)
- [AMD RyzenAI-SW LLM 範例](https://github.com/amd/RyzenAI-SW/tree/main/LLM-examples)
- [AMD 競賽工具與來源核對](AMD_COMPETITION_RESOURCE_AUDIT.md)

本版預設 `Phi-4-mini-instruct-GGUF` 是 CPU／GPU 路徑。GGUF 名稱、Lemonade recipe、驅動存在或 WebGL 顯示均不能當成 NPU 推論證據。Hybrid／FLM 路徑依官方當前模型目錄、硬體支援與記憶體條件選擇，不保證每種 AMD PC 都適用；模型來源也須符合所報競賽組別規範。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\setup_aipc.ps1 -PullModel -Model Phi-4-mini-instruct-GGUF
.\.venv\Scripts\python.exe scripts\aipc_preflight.py --configure --require-llm --model Phi-4-mini-instruct-GGUF --output evidence\aipc_preflight.json
```

`--require-llm` 在指定本機模型不存在時會回傳失敗；不加此旗標時，資料就緒仍可執行規則基線。這一步僅探查模型目錄，不等於已執行推論。

## 5. 執行真正的本機 LLM 驗收

```powershell
.\.venv\Scripts\python.exe scripts\accept_aipc.py --llm --model Phi-4-mini-instruct-GGUF
```

執行全部文字基線與三項影像驗收，另以 12 個中文合成資料呼叫本機模型；急性旗標案例會明確跳過模型，因此實際模型接納數可以小於 12。可加 `--llm-limit 3` 先做短彩排，數量和範圍將寫入原始結果。

- 要求 LLM 時，模型不可用、格式不符、整行引文改寫、回應模型不符或零筆實際模型輸出，都不能讓整體要求範圍通過。
- 原始 `llm.json` 列出每筆成功／失敗、model、recipe、response ID、usage（服務有提供時）、延遲與實際 engine 計數。
- 延遲為成功輸出的擷取／驗證時間，失敗數另列；不是 TTFT，也不包括 UI、人工或儲存時間。未量測峰值記憶體、NPU 加速比或臨床改善。
- `llm.json` 的分類指標若只跑 synthetic_demo，因沒有公開對話 section gold 而為 null；不能把缺漏規則或合成案例誤當分類準確率。

完整公開對話集的本機模型比較可另外執行，固定相同資料與切分：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_aipc.py --mode llm --model Phi-4-mini-instruct-GGUF --split acceptance --output evidence\target_llm_full.json
```

這會實際送入 400 筆研究對話和 12 筆合成資料，推論失敗列入分母。需要時間和記憶體，請先短彩排；不應直接覆寫既有結果檔案。gold 在模型輸出後才用於評分，MRI 與對話沒有病人配對。

## 6. 補上 AMD 裝置證據

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\collect_aipc_evidence.ps1 -Output evidence\target_hardware.json
```

記錄 CPU、RAM、顯示控制器、候選 NPU 驅動、Lemonade 版本與模型清單。若用 FLM，再依官方流程執行裝置驗證與 `-ValidateFLM`。裝置資料只是靜態盤點；另外保存 **同一請求時段的 runtime offload／device log、模型版本、後端版本、裝置使用證據與結果 ID**，才有資料可判斷實際 CPU／iGPU／NPU 分工。

檢查離線冷啟動、首個模型回應、連續多次請求、記憶體峰值及斷線失敗行為。這些未由本版自動填為通過。Windows CI 的套件安裝成功也不能代表實際 Ryzen AI PC／NPU 驗收成功。

## 7. 競賽五分鐘操作順序

| 時間 | 操作與應展示證據 |
|---|---|
| 0:00–0:40 | 展示實際 AMD 電腦、Lemonade 與已載入模型，說明目前實際使用 CPU／iGPU／NPU 的證據 |
| 0:40–1:40 | 完整／缺漏交接案例，點來源行回查，切換規則基線與本機模型 |
| 1:40–2:20 | 急性旗標直接交給人工處理；模型失敗明示受阻 |
| 2:20–3:30 | MRI 聯集與類別 2：原圖、遮罩、連續表面與來源網格切換，說明這是研究標籤重建 |
| 3:30–4:20 | 人工覆核、報告事實草稿、來源及版本匯出 |
| 4:20–5:00 | 開啟當天 index.html 與原始 JSON，報告實測成功率、失敗原因、延遲與尚待臨床試點的項目 |

醫療痛點是資料分散、來源難回查、交接缺漏、重複核對及等待模型造成中斷。現版以來源與人工關卡降低這些風險；是否節省時間、降低負荷或改善醫療結果，仍依完整技術文件的先導研究計畫收集，不能由軟體測試數推得。
