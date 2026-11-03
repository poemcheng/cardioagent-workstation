# DEMO-MRI-001 — 完整執行案例

這是一個已實際執行的研究 MRI 工作流程；不是臨床診斷或已驗證的 AMD 效能案例。

## 直接展示

開啟 https://poemcheng.github.io/cardioagent-workstation/showcase/

ZIP 保存完整執行證據、重跑腳本與雜湊清單。互動展示由上述網址提供；
展示頁讀取已保存的結果，不會重新執行推論。

## 重新執行

取得 cardioagent-workstation 原始碼、安裝 requirements.txt 後，在儲存庫根目錄執行：

`python scripts/run_showcase_case.py --data-dir demo-new-data --output demo-new-output`

data-dir 必須全新，腳本拒絕覆寫既有資料庫。輸入使用儲存庫內附研究 MRI 與標籤。
腳本循真正 Flask API 建立缺漏規格、確認受阻、另存完整規格、分析、示範確認、
六欄報告、draft→preliminary→final、禁止覆寫、FHIR 與稽核匯出；不使用模型 mock。

## 證據

evidence/preparation-export.json：不可變規格、來源雜湊、完整分析、網格、覆核、事件。
evidence/segmentation-mask.npy：原解析度 bool 遮罩，可核對 segmentation.mask_sha256。
evidence/blocked-order.json：缺背景資料時 HTTP 400，未建立分析。
evidence/requests.json：41 次真實 API 回應，含預期的 400 與 409。
evidence/report-history.json：報告 v1、v2、v3；final 為腳本研究展示確認。
evidence/handoff-fhir.json：Patient、ServiceRequest、DiagnosticReport collection Bundle。
evidence/audit-events.json：全事件鏈，可重算 SHA-256；未做外部錨定。
manifest.json：證據與展示資產的 SHA-256；以原始檔案位元組為準。

## 解讀

8 張 512×428 MRI；研究標籤聯集 75,846 體素。
來源表面 1,208 頂點 / 2,412 三角面；平滑顯示 65,374 頂點 / 130,744 三角面。
平滑只用於顯示；採樣比例 1 / 1 / 14.2222 不是毫米校正。
未驗證方向、實體間距與類別解剖名稱；未提供 mm³、LVEF、病灶診斷或手術建議。
標籤本身作為重建來源，不能對同一標籤回算 Dice。
本次文字為規則摘要，未呼叫 LLM；沒有訓練影像模型、NPU、Quark 或 Hybrid 執行。

## 兩次獨立執行

本證據包來自 Linux / CPython 的隔離資料目錄；分析 ID 以 case-summary.json 為準。
公開瀏覽器工作站另完成 UI 操作驗證（分析 b34697870d9d417785d01c96877c87d9），
其單次畫面顯示機器處理約 3 秒、3D 1647 ms。公開分頁的 ID、時間與資料獨立。
不可把兩次執行混成同一筆紀錄，也不可把開發環境時間當作 AMD AI PC 成績。
所有操作者均為展示帳號／腳本，不代表醫師簽核或臨床治療許可。
