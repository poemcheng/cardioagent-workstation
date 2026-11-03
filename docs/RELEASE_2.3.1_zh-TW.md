# CardioAgent 2.3.1：AMD AI PC 安裝包與一鍵驗收

2026-11-03。沿用目前的系統架構與 2.3.0 的 3D 演算法，補齊換機安裝及可追溯驗收流程。

## 本版內容

- Windows x64／CPython 3.11 的完整 Release 附件，包含固定版本 Python wheels、MTS-Dialog 1,701 筆、12 個中文合成資料與 MRI 還原套件。提供逐檔清單及整包 SHA256。
- `accept_aipc_windows.cmd` 與 `scripts/accept_aipc.py`：執行 412 筆文字規則基線、合成體模及 MRI 聯集／類別 2 的三維流程，輸出當次 HTML、JSON 及校驗檔。
- `--llm --model` 額外發出真正的本機模型請求；分開計數模型回應與急性旗標的規則跳過，模型未就緒、失敗或零筆模型輸出均不冒充模型通過。
- `aipc_preflight.py --require-llm` 與 `setup_aipc.ps1 -RequireLLM` 讓展示前檢查可以要求指定模型已準備好。
- 統一 Python、分析輸出、公開版及版本資訊為 2.3.1；舊版套件清單改由建包時產生，避免沿用過期雜湊。
- 修正 Windows 載入影像後保留檔案鎖：先檢查尺寸與總像素上限，再取得獨立像素快照並釋放檔案映射，保留來源 hash 與人工覆核檢查。
- 增加驗收及封裝回歸測試，拒絕空評測、錯誤平台 wheels、重複／越界清單及混入使用者資料或權重。

## 下載與操作

請下載此 Release 的 `CardioAgent_AMD_AIPC_..._Windows_Py311.zip`，完整解壓後依 [安裝與實機驗收指南](https://github.com/poemcheng/cardioagent-workstation/blob/main/docs/AMD_AIPC_QUICKSTART_2.3.1.md) 操作。GitHub 自動提供的 Source code.zip 沒有 Windows wheelhouse。

1. 先安裝 Python 3.11 x64（含 Python Launcher）。執行 `setup_windows.ps1 -Offline -WithVision`。
2. 執行 `accept_aipc_windows.cmd`，開啟列印路徑中的 index.html。
3. 啟動 `start_aipc_windows.cmd` 或 `start_demo_windows.cmd`，同一服務可在 `/aipc` 與 `/analysis` 間切換。
4. 安裝 Lemonade、適合硬體的 AMD 後端及已下載模型後，再執行 `scripts/accept_aipc.py --llm --model <模型ID>`。

[公開瀏覽器展示](https://poemcheng.github.io/cardioagent-workstation/) 與本機版並存；公開版沒有連接本機 LLM 或 NPU。

## 驗證範圍

Release 工作流程在 GitHub Windows runner 安裝下載的固定 wheels，執行軟體測試、7 項繪圖邏輯檢查及不含 LLM 的基線／影像驗收，通過後才發佈附件。每份包內 release_evidence 保留本次原始結果，實際測試數、Python／Windows 版本與延遲以其中紀錄為準。

影像流程使用有帳號與 CSRF 的 Flask test client，包含來源、遮罩、網格匯出與未覆核報告關卡；沒有驗證瀏覽器繪圖或真實 TCP 往返。模型權重、AMD 驅動與 Lemonade 安裝器不在包內。Windows runner 不等於 AMD Ryzen AI PC，LLM、NPU、離線冷啟動、峰值記憶體與臨床效益須在目標機分別驗證。

MRI 標籤重建不是 AI 分割預測，三角面增加不是準確率提高，未校正的 voxel／採樣比例不能用作人體毫米量測或手術規劃。

先前 46 頁技術文件固定在 2.3.0 commit `a19b765d5d5373affcad82a479ad43f2fd453df0`；本文件及新版快速指南是部署／驗收補充，歷史基線及實測來源不改標為本版裝置成果。
