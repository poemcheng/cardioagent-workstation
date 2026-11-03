# CardioAgent 2.4.0 會診準備中心

新增任務規格、來源核對、實際工具進度、共用本機模型佇列與成本情境。

- [公開操作](https://poemcheng.github.io/cardioagent-workstation/?view=workspace&revision=2.4.0)
- 本機：`http://127.0.0.1:8765/workspace`
- [操作與版本範圍](docs/RELEASE_2.4.0_zh-TW.md)
- [Windows 軟體安裝包](https://github.com/poemcheng/cardioagent-workstation/releases/latest)

以下保留既有工作站及研究功能說明；歷史版本紀錄不代表本版 AMD 或臨床驗收。

# 遠心協作 CardioAgent 2.3.1：完整影像分析與三維工作站

沿用 Flask／SQLite／本機 Lemonade／ONNX 架構，將影像來源、辨識、解釋、分割、參考比對、3D 表面、人工討論與報告串成可操作流程。

本版完成 AMD AI PC 的安裝包與一鍵驗收。請先閱讀 [2.3.1 安裝與實機驗收指南](docs/AMD_AIPC_QUICKSTART_2.3.1.md) 與 [版本修正](docs/RELEASE_2.3.1_zh-TW.md)。來源固定、人工覆核及 3D 演算法沿用既有流程；先前 Word／PDF 以其文件中的版本基準為準。

公開工作站及資料保存方式見 [公開部署說明](docs/PUBLIC_DEPLOYMENT_zh-TW.md)。GitHub下載版缺少Windows wheelhouse時，啟動器會從PyPI安裝套件並由已核對的來源壓縮檔還原影像。

## 啟動

1. Windows 11 先安裝 **Python 3.11 x64 與 Python Launcher**，完整解壓至可寫入資料夾。
2. 從完整 Release 下載並核對 SHA256；雙擊 **start_demo_windows.cmd**，以附帶 Windows wheels 離線建立環境，開啟 `http://127.0.0.1:8765/analysis`。
3. 首次建立管理者帳號（至少12字元密碼），之後保留帳號登入。
4. 選擇心臟MRI，分割來源選 **提供的研究標籤 · 非 AI 推論**、類別選聯集，按 **執行完整分析** 後查看 **3D重建**。
5. 亦可按 **載入 3D 合成體模** → **執行完整分析**，驗證已知幾何、參考比對與覆核。

預設保存於 **integrated_demo_data/**，與原版 data/、demo_data/ 分開。重啟保留分析、遮罩、版本及覆核。新的彩排資料夾可用：

```powershell
.\.venv\Scripts\python.exe run_demo.py --open-browser --data-dir integrated_demo_data_rehearsal02 --port 8766
```

Linux需自行安裝適合平台的requirements.txt後執行 `bash start_demo_linux.sh`；隨附 wheels 僅供 Windows x64 CPython3.11。套件不包含Python、AMD驅動、Lemonade安裝器或臨床模型權重。

## 完整流程

病例／影像匯入 → 像素與空間檢核 → 候選辨識或量測 → 圖像事實解釋 → 分割 → 參考比對 → 3D表面 → 處置與術前討論資料 → 人工分析覆核 → 未簽核報告 → 人工編輯與簽核 → JSON／FHIR／OBJ／STL匯出。

| 功能 | 實際行為 |
|---|---|
| AI語言模型 | 可接已下載Lemonade本機文字模型；以合成流程事實作有來源摘要，無模型時明示規則來源；自由摘要與報告事實分開 |
| 影像辨識 | 可接已登錄ST-CAC ONNX；另有合成高亮區域測試與MRI像素量測；沒有訓練權重時不冒充模型結果 |
| 圖像／證據圖解 | 原圖、候選框、遮罩、模型／參數／來源事實與互動證據圖；不冒充Grad-CAM或病理因果解釋 |
| 分割與3D | 閾值區域預覽、原檔提供研究標籤，或已登錄ONNX模型分割；使用實際遮罩建立體素邊界網格，可旋轉、縮放、匯出OBJ/STL |
| 幾何 | 完整一致DICOM依位置排序並核對方向／間距；缺失時只用voxel，不填mm³；標頭一致性不代表臨床校正 |
| 辨識與比照 | 有獨立參考時才計算IoU、Precision/Recall/F1、Dice；同源歷次設定比照不代表病程變化 |
| 處置與術前討論 | 有版本與網址的官方指南摘要、七項人工檢核、缺漏清單、PCI/CABG教育性討論；不自動選定手術或開醫囑 |
| 人工與報告 | 分析版本鎖定及退回原因；核准分析後才能帶入事實草稿，仍須手動儲存、編輯及正式報告簽核 |

## 操作與分析結果

- **docs/CardioAgent_v2_操作與分析手冊.pdf / .docx**：含安裝、逐頁操作、模型角色、實際結果與適用界線。
- **docs/INTEGRATED_USER_MANUAL_zh-TW.md**：文字操作手冊。
- **docs/PIPELINE_METHODS.md**：算法、幾何、指標及來源。
- **docs/Integrated_Analysis_Results.html**：免安裝離線結果報告與實際合成網格互動預覽，不執行後端分析。
- **evidence/integrated_sample_results.json**：本次量測的來源、參數、指標及受阻結果。
- **docs/VALIDATION.md / evidence/**：測試範圍；軟體、模型效能與AMD實機驗收分開。

合成32×96×96殼層在閾值120時得到28,736體素、89,800合成mm³、Dice1.0與9,900三角形；這是構造體模的一致性驗證。實際8張MRI在顯示強度閾值128得到295,603體素，空間資訊不足，未換算物理體積。閾值128與來源標籤的重疊Dice為0.1815；直接重建來源標籤聯集則有75,846體素、2,412三角面，使用voxel單位且不回算自身準確率。這些都不是臨床準確度。

## 本機模型

先在目標機安裝對應Lemonade/runtime及預先下載模型，再設定：

```powershell
.\.venv\Scripts\python.exe scripts\configure_lemonade.py --list
.\.venv\Scripts\python.exe scripts\configure_lemonade.py --model "實際文字模型ID"
.\.venv\Scripts\python.exe scripts\configure_lemonade.py --speech-model "實際語音模型ID"
```

重啟CardioAgent使預設模型設定生效。只接受已下載、用途相符、無雲端路由模型。**模型狀態**顯示實際配置；recipe或裝置清單不等於NPU執行證據。

原CT候選模型使用 `scripts/register_model.py`；可選解剖分割模型使用 `scripts/register_segmentation.py --help`。先以 `setup_windows.ps1 -Offline -WithVision` 安裝ONNX Runtime。分割契約固定 `[1,1,H,W]` float32→`[1,C,H,W]` logits，明確窗位、argmax目標類別、SHA256與來源；不任意縮放。類別名稱由登錄者聲明，未經本平台驗證，不等於解剖真值。

## 驗證與備份

```powershell
.\.venv\Scripts\python.exe scripts\demo_preflight.py --output evidence\target_preflight.json
.\.venv\Scripts\python.exe scripts\verify_package.py
```

開發驗證另需適合平台的requirements-dev.txt、Node與jsdom；詳見VALIDATION.md。`scripts/analyze_samples.py` 可重跑內附資料；Matplotlib僅用於科學結果圖，不是操作系統的必要依賴。

備份前停止服務，保存整個integrated_demo_data/（含SQLite、analyses、遮罩及歷史版本）。本版只接受loopback、有帳號與角色／CSRF；尚未接入醫院SSO、HIS、PACS或憑證式簽章，資料未做靜態加密。使用授權且已去識別資料；影像與來源授權不因本套件而改變。

本版提供研究流程與模型接入工具；未附臨床訓練權重，未完成AMD實機、心臟解剖／疾病效能或手術規劃驗證。單純CAC、少量MRI、閾值網格與勾選清單不能替代完整臨床證據及Heart Team決策。
