# CardioAgent 公開部署與使用

公開工作站：https://poemcheng.github.io/cardioagent-workstation/

原始碼：https://github.com/poemcheng/cardioagent-workstation

公開網址的部署與操作驗收狀態以 GitHub Pages 及本次交付說明為準。

## 公開工作站

開啟網址後，等待首次下載 Python 與數值運算元件。程式使用固定 Pyodide 0.29.4，於 Web Worker 執行原有 Flask／NumPy／SQLite 程式，前端沿用影像、3D與報告工作站。公開網站提供程式及內附研究影像，不接收或共用訪客的影像與分析紀錄。

1. 預設進入影像分析；選擇 ACDC MRI，分割來源選「提供的研究標籤」，執行並查看原圖、遮罩及3D。
2. 也可選「閾值分割」並修改閾值，重新計算與參考標籤的重疊；不以提供標籤的重建結果自評 Dice。
3. 載入合成體模，執行後查看已知幾何、3D與遮罩比對。
4. 修改術前討論先保存，再明確勾選分析覆核；核准後帶入未簽核報告。
5. 在報告工作站人工編輯、保存、送初步與展示簽核，也可匯出 JSON、FHIR、OBJ／STL；匯出準備完成後，在對話框按下載連結保存。

所有資料僅在這個分頁的記憶體工作區；關閉、重新整理或重新啟動會清除。需要保存的結果請先下載。不同訪客及不同分頁互相隔離。公開版的 browser-visitor 是展示操作者，不代表經認證的醫事人員；展示確認不具有院內或憑證簽章的效力。

公開版未連接 Lemonade、本機模型或 AMD NPU；語言整理明示規則來源。缺少影像模型時保持受阻，不用展示結果替代臨床模型。3D的體素／物理單位、標籤來源及臨床驗證限制沿用原工作站規則。

## AMD AI PC 本機版

完整下載 GitHub 專案，安裝 Python 3.11 x64 與 Python Launcher，解壓到可寫入資料夾後雙擊 start_demo_windows.cmd。GitHub來源不含 Windows wheelhouse，首次從 PyPI 安裝必要套件；原本包含 wheelhouse 的交付套件仍可離線安裝。啟動器會從已核對SHA256的 docs/cardio-runtime.zip 還原影像陣列。

本機版仍使用帳號、角色與 CSRF，預設只接受loopback連線，並將資料持續保存於 integrated_demo_data/。要接入已下載的Lemonade模型或已註冊ONNX模型，依README及操作手冊設定；公開網頁不會連入訪客的localhost服務。

## 維護與建置

修改Python、模板或前端後，於已具備來源影像及純Python wheels的開發環境執行：

```text
python scripts/build_public.py
python scripts/validate_demo.py --dom --node-modules 含jsdom的node_modules目錄
```

建置輸出位於docs/：包含工作站前端、固定套件的純Python wheels、實際Python及影像來源壓縮檔，以及完整性清單。GitHub Pages以main分支的/docs發佈；docs/.nojekyll保留原檔。

Pyodide的NumPy／Pillow／MarkupSafe使用其對應WebAssembly套件，版本可與Windows CPython套件不同，需各自驗證；不能以原本Linux測試推定瀏覽器已通過。本機CPU、瀏覽器WebAssembly與AMD NPU的執行證據分開記錄。
