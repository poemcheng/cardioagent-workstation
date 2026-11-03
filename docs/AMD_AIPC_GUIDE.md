# CardioAgent 2.2：AMD AI PC 會診交接展示

最新完整安裝包與一鍵驗收請先看 [2.3.1 換機指南](AMD_AIPC_QUICKSTART_2.3.1.md) 及 [Release 修正](RELEASE_2.3.1_zh-TW.md)。本頁保留模型與研究資料的詳細說明，歷史數字以各自版本為準。

版本：2.2.0 / 2026-11-03（競賽資源核對修正）。定位：可執行的研究與競賽展示。入口是 `start_aipc_windows.cmd`；原有影像、3D、人工覆核與報告工作站保留。

## 1. 這次處理的醫療流程痛點

醫療交談、藥單、檢查來源與影像分散，整理會診資料時需要重複搬寫，缺漏也不容易被看見。本版本把工作收斂成「會診前資料整理」，輸出可回查的摘錄與待補欄位，交由人員核對。

| 痛點 | 已實作行為 | 應驗證的效果 |
|---|---|---|
| 交談轉成文件需要重複閱讀與輸入 | 地端文字模型選取完整原文行，附前一行脈絡、來源位置與匯出紀錄 | 人員完成同一份交接所需總時間，包含修正時間 |
| 藥單、過敏、腎功能、心功能來源等交接資料缺漏 | 依人工提供的結構欄位列出待補項目；未知急性旗標仍列缺漏 | 缺漏率、誤報率；本次只有合成規則驗證 |
| 摘錄與來源難以核對 | 每筆摘錄可跳回整行原文；拒絕改寫數字、刪除原句中的否定詞；存檔附 SHA-256 與稽核事件 | 人員核對來源所需時間與重大資訊遺漏 |

這些是待驗證的效益，不是已證明的臨床改善。2025 年 AI scribe 觀察研究顯示部分文書與 EHR 時間下降的關聯，但未見所有工作負荷指標改善；不能把其他產品的結果當成本系統的成效。[1]

## 2. AI PC 架構與目標機

規劃基準：Windows 11 x64、AMD Ryzen AI 300 系列、16 GB RAM，例如 Ryzen AI 7 350 / PN54。實際型號、Windows build、記憶體與驅動由採證腳本記錄，不依 CPU 名稱宣稱 NPU 已執行。

| 元件 | 本版本工作 | 證據邊界 |
|---|---|---|
| CPU / Python | 資料解析、雜湊、來源核對、結構缺漏規則、影像幾何與量測 | CPU 規則基線不是語言模型 |
| 本機文字預設 | Lemonade + `Phi-4-mini-instruct-GGUF` | CPU/GPU 路徑；實際 backend 需實測，不能稱 NPU |
| NPU 候選路徑 | 來源已核對且匹配 runtime 的 Phi Hybrid / NPU 模型 | 必須在目標機下載、實跑並保留裝置證據；版本不能混用 |
| iGPU / WebGL | 原有影像與 3D 互動顯示；另可評估支援模型的 GPU 推論 | DirectML 是 GPU 路徑，不能稱為 NPU |
| 瀏覽器與 SQLite | 本機登入、交談閱覽、摘錄回查、人工覆核、紀錄保存 | 工作站只監聽 127.0.0.1；不是院內正式 HIS/PACS 連線 |

競賽預設改用 Microsoft Phi 基礎模型，原始與 GGUF 模型卡均標示 MIT；另保留 AMD NPU + iGPU 的混合路徑。若兼報開源 AI 模型組，須遵守該組核心模型來源限制；詳見 [競賽資源核對表](AMD_COMPETITION_RESOURCE_AUDIT.md)。AMD 官方另列 FLM 與 OGA 路徑。[2][3] 16 GB 先採單一 4B 模型、單次一個請求；不要同時載入大型文字、視覺及語音模型。選單只接受模型服務宣告已下載、用途相符且無雲端路由的模型。Recipe 只代表配置，不是逐請求的硬體證明。

文字模型在此做「段落分類與整行原文擷取」，不補寫未提供的數值，不開立醫囑，不決定手術。沒有可用 LLM 時，只有使用者明確點選的規則基線；模型失敗不會被規則結果冒充。

## 3. 已下載並隨套件提供的資料

| 資料 | 實際數量 | 用途與限制 |
|---|---|---|
| MTS-Dialog 原始 CSV | 1,701 筆：訓練 1,201、驗證 100、測試1 200、測試2 200 | 英文一般醫療交談，20 種段落標籤；研究分類與來源擷取，不代表中文心臟專科能力 |
| 中文合成流程案例 | 12 筆 | 完整交接、藥單/過敏/腎功能/心功能缺漏、急性與未知旗標、來源追溯；是作者設計的流程測試，沒有病人 |
| 原有心臟 MRI 樣本 | 8 個影像切片與提供的研究參考標註 | 支援閱片與參考表面 3D；不是新訓練的 AI 分割，也不是完整手術規劃資料 |

MTS-Dialog 官方來源為 `abachaa/MTS-Dialog`，固定 commit `3ff0801933608d6f570468c13125125fb5cabdea`。原始檔保存在 `datasets/mts_dialog/raw/`，包含 CC BY 4.0 授權；作者、論文引用、上游 README、檔案大小、Git blob SHA 與 SHA-256 均保留。[4]

`python scripts/prepare_aipc_data.py` 會先核對上游原始 bytes，再建立 1,713 筆 `cases.jsonl` 與 manifest。原始切分不調整；查到 3 組完全相同的交談重複：訓練組內 2 組、測試2內 1 組，沒有查到跨切分的完全相同交談。這不證明病人彼此獨立，也不排除語意重複或預訓練污染。

MTS 標註答案僅供離線評估，在模型請求與病例 API 中移除。交談和 MRI 不是同一病人的配對資料，不會自動合併。MRI 未取得可靠的物理間距與解剖類別映射時，保留像素/體素單位，不捏造 mm³ 或冠狀動脈狹窄率。

原始 CSV 為唯讀來源；不在此次展示微調模型。先使用官方測試切分評估既有模型，再考慮額外資料或訓練。大型 ACDC / MSD 等資料沒有在這版宣稱已下載。

## 4. 搬到 Windows AMD AI PC

1. 解壓完整 DemoKit 到一般使用者可寫入的資料夾，例如 `C:\CardioAgent`。先安裝 Python 3.11 x64 與 Python launcher。套件包含核心依賴的 Windows wheels，沒有包含 Python 安裝程式、LLM 權重或 AMD 驅動。
2. 雙擊 `start_aipc_windows.cmd`。它會建立虛擬環境、核對資料並啟動 `http://127.0.0.1:8765/aipc`。第一次建立自己的本機帳號；沒有預設帳密。沒有模型時先確認規則基線與缺漏流程。
3. 從官方網站安裝 Lemonade，依官方條件完成 Ryzen AI / NPU 驅動。預設 GGUF 使用 llama.cpp backend；選擇 Hybrid 或 NPU 模型時，另依官方文件安裝匹配的 runtime。不要把 GGUF 當作 NPU 推論。[2][3]
4. 啟動 Lemonade，於專案根目錄執行下列命令。第一次需要網路下載模型；下載完的展示可在停用外網後重跑。這一步不會更動 BIOS 或電源策略。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_aipc.ps1 -PullModel
```

等價的模型下載核心命令為 `lemonade pull Phi-4-mini-instruct-GGUF`；模型卡與來源見競賽核對表。[5] 若官方模型目錄更新或該硬體不支援，使用官方可用模型 ID，明確記錄實際 ID / recipe，不能用 CPU 備援宣稱 NPU 成功。

5. 回到畫面按「重新檢查模型」，選已下載的本機模型，按「本機 LLM 擷取」。匯出 JSON 的 `engine.source` 應為 `lemonade_local_model`，並保留實際模型 ID、recipe、usage 與耗時。選 `synthetic-07` 時會直接顯示急性旗標優先，這次刻意不呼叫模型。

若未連接：執行 `.\.venv\Scripts\python.exe scripts\aipc_preflight.py --configure`。腳本檢查 127.0.0.1 上的 `/api/v1` 與 `/v1`，不允許雲端替代端點。Lemonade 的目前官方 OpenAI 相容入口為 `http://localhost:13305/api/v1`；程式使用同一 loopback 的 127.0.0.1。[2]

停止：在啟動視窗按 Ctrl+C。本機資料保留在 `integrated_demo_data`。完整套件中的示範資料可離線使用；公開 GitHub Pages 是原有瀏覽器流程版本，不能用它核對你電腦的 NPU。

## 5. 五分鐘競賽展示順序

| 時間 | 操作 | 評審能確認的事 |
|---|---|---|
| 0:00-0:40 | 展示資料與 runtime 狀態，說明三個資料來源沒有病人配對 | 資料來自哪裡、哪些是真實公開研究資料、哪些是合成案例 |
| 0:40-1:40 | 選 `synthetic-01`，用本機 LLM 擷取，點「回到來源」並匯出 JSON | 本機模型確實被呼叫；每筆引文可核對來源 |
| 1:40-2:20 | 選 `synthetic-02`，再選 `synthetic-08` 執行 | 缺藥單與未知急性旗標保持待補，沒有被模型猜成完整 |
| 2:20-3:00 | 選 `synthetic-07`，點本機 LLM 按鈕 | 急性人工旗標不等待文字模型，沒有自動醫囑 |
| 3:00-4:00 | 選 MTS 測試組的同一筆，分別跑規則與 LLM | 相同輸入的配置比較；擷取驗證不等於醫療正確性 |
| 4:00-5:00 | 進「影像分析/3D」，顯示既有 MRI/參考模型；開啟採證結果 | 影像工具與地端文字服務共同運作，但來源與病人分開 |

若模型尚未安裝，展示只能稱「CPU 規則與資料流程展示」，不要稱 NPU AI 成果。

## 6. 實測與 AI PC 驗收

這個版本已在 Linux 軟體環境執行完整測試及 400 筆公開測試交談 + 12 筆合成案例的規則基線。實測數值以 `evidence/aipc_baseline_results.json` 為準。規則分類準確率不是臨床準確率，合成案例的欄位核對也不是臨床安全驗證。LLM / NPU 尚待 Windows 目標機執行。

本次結果：189 項 Python 測試通過；23 項真實 HTTP / DOM 操作檢查通過，沒有執行視覺瀏覽器驗收。公開測試交談的規則分類為 189 / 400 = 47.25%，有支持樣本類別的 macro F1 為 0.3094。412 / 412 份規則輸出通過整行原文核對；12 / 12 合成案例的缺漏規則符合預設答案。這些數字不能推論 LLM 品質、臨床安全性或 NPU 加速倍數。

先收集目標機硬體與驅動，再執行固定輸入的模型評估：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\collect_aipc_evidence.ps1
.\.venv\Scripts\python.exe scripts\evaluate_aipc.py --mode llm --model Phi-4-mini-instruct-GGUF --split test_1 --output evidence\aipc_llm_test1.json
lemonade bench --json --output evidence\lemonade_bench.json Phi-4-mini-instruct-GGUF
```

先以 `--limit 10` 彩排，再跑完整測試組。評估程式在推論後才讀 gold，將失敗/不合格式輸出保留在分母，記錄各類支持數、分類準確率、macro F1、成功輸出的 p50/p95 耗時及失敗原因。此 API 沒有串流，所以不報 TTFT。`lemonade bench` 是另一個微基準，不等於應用任務耗時。[5]

| 驗收項目 | 需要保留的證據 | 本版狀態 |
|---|---|---|
| 資料可重建 | 上游 commit、CSV hash、manifest、官方切分 | 已完成 |
| 工作流程可執行 | 認證、API、引文/數字/否定詞核對、急性與缺漏測試 | 已完成軟體測試 |
| 本機 LLM 有回應 | 實機輸出 JSON、模型 ID、recipe、usage、runtime 版本 | 待目標機 |
| NPU 確實承擔該請求 | 同一時間的 runtime 裝置選擇/卸載紀錄、硬體/驅動資料、NPU 活動佐證 | 待目標機；僅利用率或 recipe 不足以獨立證明 |
| 效能/記憶體可接受 | 相同輸入、warm/cold 分開、p50/p95、峰值 RAM/共享顯存、失敗率 | 待目標機；沒有先寫入速度提升倍數 |
| 工作負擔是否下降 | 人工 vs 輔助流程的交叉順序測試，計入修正時間、重大缺漏與人員評分 | 尚未做臨床使用研究 |

比較 FLM NPU 模型與 GGUF CPU/GPU 模型時，量化格式、模型版本及後端可能不同，只能稱配置比較。要歸因為硬體加速，需固定可比模型、輸入、輸出長度、上下文與測試條件，並記錄 runtime 證據。

建議先邀請實際交接人員使用合成資料做小型任務測試，隨機交換人工/輔助順序。預先定義重大遺漏、正確完成與總耗時，不將自動通過的引文檢查當成人員滿意度或臨床安全性。接院內系統前仍需機構資安、介接、權限與臨床驗證流程。

## 7. 參考資料與授權

1. Pearlman K, et al. Use of an AI Scribe and Electronic Health Record Efficiency. JAMA Network Open. 2025;8(10):e2537000. https://jamanetwork.com/journals/jamanetworkopen/fullarticle/2839929
2. AMD. Getting started with Lemonade. https://developer.amd.com/playbooks/lemonade-getting-started/
3. AMD Ryzen AI Software. LLM overview / system requirements. https://ryzenai.docs.amd.com/en/latest/llm/overview.html
4. Ben Abacha A, Yim W, Fan Y, Lin T. An Empirical Study of Clinical Note Generation from Doctor-Patient Encounters. EACL 2023, 2291-2302. https://aclanthology.org/2023.eacl-main.168/ ; source https://github.com/abachaa/MTS-Dialog ; CC BY 4.0.
5. Lemonade CLI guide. https://lemonade-server.ai/docs/guide/cli/

資料檔與專案程式的授權分開管理。MTS 原始授權隨附；模型遵循下載時的模型授權；既有 MRI 研究樣本不得因展示套件存在就推定取得新的商業/臨床使用許可。
