# CardioAgent 心臟影像研究工作站

**公開操作網址：[開啟工作站](https://poemcheng.github.io/cardioagent-workstation/)**

**AMD AI PC 2.2 本機展示：**新增會診交談擷取、來源回查與交接缺漏檢查；已下載 MTS-Dialog 1,701 筆及建立 12 個中文合成流程案例。Windows 解壓後啟動 `start_aipc_windows.cmd`，操作與模型下載見 [AI PC 操作與驗收指南](docs/AMD_AIPC_GUIDE.md)。此功能在本機執行，公開網頁版維持原有影像流程。LLM 權重及 NPU 驅動需在目標機安裝；目前未宣稱 NPU 實測完成。

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
