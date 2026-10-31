# CardioAgent 2.1.0 驗證與結果界線

2026-10-31於Linux／CPython 3.12.14執行；完整版本見evidence/integrated_validation.json。

| 類型 | 結果 | 證據 |
|---|---|---|
| 軟體與數值回歸 | 176/176，0失敗 | integrated_unit_tests.txt |
| 原工作站介面／HTTP | 16/16，88次真實HTTP | competition_dom.json |
| 完整分析介面／HTTP | 15/15，50次真實HTTP | integrated_dom.json |
| 三維繪圖邏輯 | 6/6 | mesh_viewer_checks.txt |
| 實際資料 | 合成解析體模及原檔提供MRI影像／標籤，含閾值敏感度與來源比對 | integrated_sample_results.json |

軟體測試涵蓋角色／CSRF、病例與切片一致性、補件、急性優先、慢LLM不阻塞急性流程、來源雜湊、分割模型契約、已提供標籤、DICOM方向與間距、網格封閉／尺度、指標、資料保存及版本、圖解、討論清單與報告交接。沒有真值時不虛構準確率；直接用標籤重建時不做同源自我評分。

v2.0.1新增14項回歸，涵蓋伺服器覆核確認、來源／幾何／標籤／遮罩及分析歷史完整性、急性流程、Windows舊CRLF文字紀錄、未定義欄位與啟動檢查一致性。DOM另測未保存討論的取消操作，以及延遲的真實404病例載入失敗。詳細修正見RELEASE_2.0.1_zh-TW.md。

v2.1.0另新增5項瀏覽器WSGI傳輸／隔離／影像／報告測試及2項來源壓縮檔還原測試。公開網站的WebAssembly執行與視覺驗收另行記錄，不以Linux測試替代。

## 本次實際數值

- 合成殼層：32×96×96，閾值120，28,736體素，89,800合成mm³，Dice1.0，9,900三角面。此結果是構造體模一致性，不是臨床效能。
- 實際MRI閾值128：295,603體素；與原檔研究標籤非零聯集重疊Dice0.1814946、IoU0.0998043。標籤映射、標註者、訓練資料獨立性與臨床效能未核實，不能當作臨床測試集結果。
- 實際MRI提供標籤重建：75,846體素，2,412三角面，voxel單位；沒有完整間距，不換算mm³。這是來源標籤表面，沒有執行AI解剖辨識。

## 實驗不涵蓋的範圍

HTTP來自實際Flask／Waitress。DOM測試未偽造fetch資料，但Canvas與影像載入事件使用替身；另以實際PNG回應核對像素。因此瀏覽器視覺與GPU繪圖驗收保持false。

ONNX契約測試使用特製測試圖，確實執行ORT，沒有載入臨床權重。LLM／ASR協定以測試回應驗證；本機沒有實際語言或語音模型。未完成Windows安裝、AMD PN54／NPU、心臟模型準確度、院內互通或手術規劃驗證。套件內數值不能作個案處置或手術適應症判斷。

Word/PDF手冊已逐頁render檢查，這與系統瀏覽器視覺驗收不同。靜態結果HTML是離線報告／網格檢視，不提供後端分析服務。

## 重現

```text
python scripts/validate_demo.py
python scripts/validate_demo.py --dom --node-modules 含jsdom的node_modules目錄
python scripts/analyze_samples.py
```

開發驗證需平台相符requirements-dev.txt，DOM另需Node/jsdom。驗證器使用暫存資料；不操作既有展示者資料。Matplotlib僅用於結果圖。Windows現場另跑demo_preflight.py並保存裝置、模型、冷啟動、尖峰RAM及實際運算後端紀錄。

## 公開網址實際驗收

已在公開GitHub Pages以桌面Chrome執行原Python流程，非Canvas／影像替身：提供標籤75,846體素／2,412三角面、MRI閾值128的Dice0.1815、WebGL、證據圖、未保存檢核、分析覆核與報告草稿交接均已觀察。第二分頁的分析與報告為獨立工作區。完整紀錄及驗收限制見evidence/public_browser_verification.json。
