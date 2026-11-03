此目錄不含 ST-CAC 訓練權重。請使用 register_model.py 登錄自行訓練且已授權的 ONNX。
模型須為靜態 float32 [1,3,512,512]、單類 YOLOv8 原始輸出 [1,5,N]，無 NMS。
通道必須依序是 previous / center / next；請先用與訓練相同的 PNG 前處理驗證。
一般 COCO YOLO 權重不適用 CAC；不得用其結果替代 ST-CAC。

2.4.1 起登錄器接受 --confidence（預設 0.25）與 --nms-iou（預設 0.5），
存入 manifest.postprocessing 並在推論輸出記錄。manifest 優先於 config；舊 manifest
沿用 config。原稿 NMS 0.7 若適用，必須明確指定並另做模型等價驗證，不能與配對 IoU 混用。
同次模型鏈驗收見 docs/RELEASE_2.4.1_zh-TW.md。
