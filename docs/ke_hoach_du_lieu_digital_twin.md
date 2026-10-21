# Kế hoạch dữ liệu: từ mô phỏng synthetic → ABM hiệu chỉnh → digital twin

*Bản nháp. Đi kèm mã nguồn trong repo này. Các con số ví dụ là gợi ý thiết kế, không phải dữ liệu thật.*

---

## 0. Định vị cho bài báo: bạn đang ở đâu và cần đến đâu

| Mức | Tên gọi | Điều kiện | Repo hiện tại |
|---|---|---|---|
| 1 | ABM synthetic | Tham số giả định, chứng minh cơ chế | ✅ đang ở đây |
| 2 | ABM hiệu chỉnh (calibrated) | Tham số ước lượng từ dữ liệu thật của khu nghỉ | cần dữ liệu §1–§4 |
| 3 | ABM kiểm định (validated) | Dự báo đúng trên **dữ liệu ngày giữ lại** (holdout) chưa dùng để hiệu chỉnh | cần ≥ 2 đợt dữ liệu |
| 4 | Digital shadow | Dữ liệu cảm biến chảy **một chiều, gần thời gian thực** vào mô hình | cần §2 chạy liên tục |
| 5 | Digital twin | Thêm **vòng phản hồi**: mô hình đề xuất → vận hành thay đổi → đo lại | cần hợp tác vận hành |

**Gợi ý cho bài báo:** đóng góp khả thi và trung thực nhất là **mức 2–3**: một ABM hotel–park được hiệu chỉnh và kiểm
định, kèm đánh giá chính sách. Thêm một **nguyên mẫu digital shadow** dùng 2–3 cảm biến đếm người làm minh chứng
mức 4. Đừng gọi là "digital twin" nếu chưa có vòng phản hồi (mức 5); reviewer sẽ bắt lỗi chỗ này.

Khung báo cáo nên dùng:
- **ODD+D protocol** (Overview, Design concepts, Details + Decision) để mô tả ABM.
- **Pattern-oriented modelling** để hiệu chỉnh theo nhiều "mẫu hình" cùng lúc: đường cong hàng chờ theo giờ, phân bố giờ
  về khách sạn, hình dạng phụ tải điện.
- **Phân tích độ nhạy** (Morris → Sobol) cho các trọng số hữu dụng và tham số nhiệt.

---

## 1. Danh mục dữ liệu cần có

Ký hiệu ưu tiên: **B** = bắt buộc cho bài báo · **N** = nên có · **T** = tùy chọn / giai đoạn sau.

### 1.1 Khách sạn (PMS, BMS, vận hành)

| Dữ liệu | Độ phân giải | Ưu tiên | Dùng để | Thay thế tham số nào trong repo |
|---|---|---|---|---|
| Số phòng bán theo đêm, check-in/check-out (giờ) | theo ngày/giờ | **B** | tạo dân số khách, công suất | `hotel.occupancy`, A02–A03 |
| Quy mô & thành phần nhóm (người lớn/trẻ em, **ẩn danh, gộp**) | theo booking | **B** | phân phối nhóm | `group_size_probs`, A05–A07 |
| Quốc tịch/ngôn ngữ (gộp) | theo tháng | N | phân khúc hành vi | `preference_means` |
| Log thẻ từ / cảm biến hiện diện phòng | sự kiện | **B** | giờ rời/về phòng thật | `wake`, `park_return`, A12 |
| BMS phòng: nhiệt độ, điểm đặt, trạng thái FCU | 5–15 phút | **B** | hiệu chỉnh mô hình nhiệt C, UA, COP | `rooms.*`, A26–A29 |
| Tỷ lệ phòng "bypass" thẻ từ | ước lượng từ log | N | baseline điều hòa | `keycard_bypass_share` |
| Công tơ điện theo bộ phận (Excel Q2.9 nói **đã có**) | 15 phút | **B** | **kiểm định** điện theo mục đích | toàn bộ `END_USES` |
| Chiller trung tâm / bơm / AHU khu chung | 15 phút | **B** | tải khu chung (hiện là giả định lớn nhất) | `hotel_common`, A34 |
| Log buồng phòng: bắt đầu/kết thúc mỗi phòng, DND, thay khăn/ga | sự kiện | **B** | năng suất, gián đoạn, nhu cầu giặt | `housekeeping.*`, A39–A41 |
| Giặt là: số mẻ, kg, kWh máy | theo mẻ | N | năng lượng giặt | `laundry.*`, A42–A43 |
| Nhà hàng: số suất theo giờ (POS) | giờ | **B** | kiểm định hành vi ăn | `restaurant` |
| Nước nóng: kWh bơm nhiệt / nồi hơi | giờ | N | tải nước nóng | A33 |

### 1.2 Công viên giải trí

| Dữ liệu | Độ phân giải | Ưu tiên | Dùng để |
|---|---|---|---|
| Lượt vào/ra cổng (kể cả khách lưu trú vs khách ngày) | 15 phút | **B** | tốc độ đến λ(t), khách ngày |
| Log điều phối từng trò: giờ chạy, số khách/chuyến, thời gian dừng | theo chuyến | **B** | chu kỳ, hệ số tải, `kwh_per_cycle` |
| Thời gian chờ niêm yết & đo thực tế | 15 phút | **B** | **kiểm định** hàng chờ |
| Công suất điện từng trò / khu (nếu có công tơ) | 15 phút | **B** | tải nền vs tải theo chuyến |
| Lịch & số khách mỗi suất diễn | theo suất | N | mô hình show |
| Giờ mở cửa, quy tắc chiều cao/độ tuổi, sức chứa thật | tĩnh | **B** | thay nhãn chung bằng dữ kiện xác minh |
| Bản đồ GIS / khoảng cách đi bộ thật | tĩnh | **B** | thay đồ thị sơ đồ |
| Doanh thu F&B theo điểm bán | giờ | T | hành vi chi tiêu |

### 1.3 Di chuyển kết nối
- Cáp treo / tàu cao tốc / xe điện nội khu: lịch, tải mỗi chuyến, thời gian hành trình, kWh. **B** nếu đưa điện giao
  thông vào ranh giới; còn lại **N**.

### 1.4 Môi trường & phát thải
- Trạm thời tiết tại chỗ hoặc gần nhất: nhiệt độ, độ ẩm, bức xạ, theo giờ. **B**
- Hệ số phát thải lưới điện Việt Nam do cơ quan nhà nước công bố theo năm (ghi rõ năm, nguồn, phương pháp). **B**.
  Thay hệ số minh họa 0,60.
- Sản lượng điện mặt trời tại chỗ (Excel Q1.20 có số theo tháng; cần số 15 phút). **N**

### 1.5 Hành vi khách (phần "lõi" của ABM)
- **Chuỗi điểm đến có thời gian** của từng nhóm (ẩn danh) → ma trận Markov (§2) và kiểm định quỹ đạo ABM. **B**
- **Thời gian chờ chịu được & lý do bỏ hàng.** **B**
- **Trọng số lựa chọn** (khoảng cách vs chờ vs sở thích vs nóng) từ thí nghiệm lựa chọn có chủ đích (§4). **B**
- Hành vi điều hòa / khăn tắm / chấp nhận gợi ý. **B**

---

## 2. Ý tưởng 1: Bản đồ chọn khu → "khách chờ lâu ở đâu, đi tiếp đâu" bằng chuỗi Markov

### 2.1 Mô hình
- **Trạng thái** = khu vực: các trò chơi, nhà hàng, khu nghỉ, cổng, "về khách sạn" (trạng thái hấp thụ).
- **Ma trận chuyển** `P[i→j]` = số lần khách đi từ i sang j chia cho số lần rời i.
- Nên dùng **Markov không thuần nhất theo thời gian**: một ma trận cho mỗi khung giờ (sáng / trưa / chiều / tối) × phân
  khúc (gia đình / cặp đôi / nhóm bạn).
- Thêm **thời gian lưu lại** (dwell time) cho mỗi trạng thái. Như vậy thành mô hình **semi-Markov**, đủ để dự báo
  "bao lâu nữa khách rời khu này".
- Kiểm định:
  - So sánh bậc 1 vs bậc 2 bằng log-likelihood / AIC trên dữ liệu giữ lại.
  - So sánh dòng khách dự báo vs dòng đếm bởi cảm biến (§3).

### 2.2 Giao diện bản đồ (tích hợp vào bản đồ 3D có sẵn)
Bấm vào một khu, bảng bên cạnh hiển thị:
1. Thời gian chờ trung bình / P90 theo giờ (bản đồ nhiệt).
2. **Top-3 điểm đến tiếp theo** kèm xác suất, ví dụ "62% → Nhà hàng, 21% → Khu nghỉ, 9% → Về KS".
3. Biểu đồ Sankey dòng khách vào/ra khu.

### 2.3 Dữ liệu cần cho Markov
- Mỗi bản ghi: `id_ẩn_danh, khu, giờ_vào, giờ_ra, phân_khúc`.
- Nguồn có thể dùng: nhật ký hành trình trong khảo sát (§4, bản đồ đánh số), quét vé/vòng tay tại cổng trò chơi
  (nếu có), app.
- Cỡ mẫu: mỗi **hàng** của ma trận nên có ≥ 30–50 lượt chuyển. Ví dụ 12 khu × 4 khung giờ × 3 phân khúc ≈ 144 hàng,
  tức cần vài nghìn lượt chuyển. Nếu ít dữ liệu, gộp khung giờ/phân khúc hoặc dùng làm mượt Bayes (Dirichlet prior).

### 2.4 Vị trí trong bài báo
Markov là **mô hình mô tả/dự báo ngắn hạn**. Nó không trả lời được "nếu đổi chính sách thì sao", vì xác suất chuyển
thay đổi khi chính sách thay đổi. Hướng viết tốt:
- dùng **Markov làm baseline thực nghiệm**;
- so sánh khả năng dự báo của ABM (dựa trên hữu dụng) với Markov trên cùng dữ liệu giữ lại;
- dùng ABM cho câu hỏi chính sách.

Đây là một đóng góp phương pháp rõ ràng.

---

## 3. Ý tưởng 2: Cảm biến đếm người cắm USB (nhà ăn, cửa, lối vào hàng chờ)

### 3.1 Lựa chọn công nghệ

| Loại | Đếm hướng vào/ra | Độ chính xác với nhóm đông | Quyền riêng tư | Chi phí / độ khó |
|---|---|---|---|---|
| Tia hồng ngoại cắt ngang (2 tia → biết hướng) + vi điều khiển USB | có | thấp (đếm nhầm người đi sát nhau) | rất tốt | rất rẻ, dễ |
| Cảm biến ToF / mmWave radar gắn trần | có | khá–tốt | rất tốt (không có hình) | trung bình |
| Cảm biến nhiệt độ phân giải thấp (8×8 … 32×24) | có | khá | tốt | trung bình |
| Camera + nhận diện người **trên thiết bị** (mini PC / Raspberry Pi / Jetson), **chỉ xuất số đếm, không lưu ảnh** | có | tốt nhất | cần xin phép, thông báo, đánh giá pháp lý | trung bình–cao |
| Quét WiFi/BLE | không | kém (MAC ngẫu nhiên) | nhạy cảm | rẻ; **không khuyến nghị** |

**Đề xuất:**
- Pha thử nghiệm: hồng ngoại hai tia hoặc ToF tại cửa nhà hàng và lối vào/ra của 1–2 hàng chờ trò chơi.
- Camera chỉ dùng khi đơn vị vận hành đồng ý bằng văn bản và đã kiểm tra với pháp chế. Việt Nam có quy định bảo vệ dữ liệu cá
  nhân (Nghị định 13/2023/NĐ-CP và luật mới về bảo vệ dữ liệu cá nhân), nên **cần xác minh quy định hiện hành**.

### 3.2 Vị trí lắp & đại lượng suy ra
- **Cửa nhà hàng** (vào/ra) → số khách đang có mặt `L(t)` = tích lũy (vào − ra) → so với `active` của `HOTEL_RESTAURANT`.
- **Lối vào và lối ra hàng chờ trò chơi** → độ dài hàng `L(t)`. Kết hợp tốc độ đến `λ(t)`, định luật Little cho
  **thời gian chờ** `W ≈ L / λ`. Đây là dữ liệu kiểm định hàng chờ quý nhất.
- **Cổng công viên phía khách sạn** → khách lưu trú đến/về theo giờ → hiệu chỉnh `park_departure`, `park_return`.
- **Hành lang tầng khách sạn** (nếu được phép) → đối chiếu log thẻ từ.

### 3.3 Định dạng dữ liệu (mỗi thiết bị ghi CSV hoặc gửi MQTT)
```
sensor_id, location_id, timestamp_utc, interval_s, count_in, count_out, battery_or_status, firmware
```
- Đồng bộ giờ bằng NTP; ghi theo khoảng 1 phút, tổng hợp 15 phút khi đưa vào mô hình.
- **Hiệu chuẩn:** ≥ 3 buổi × 1 giờ đếm tay song song ở giờ thấp điểm và cao điểm. Báo cáo sai số (MAPE, độ chệch theo
  quy mô nhóm). Sửa sai lệch bằng hệ số hiệu chỉnh.
- Kiểm tra trôi số: mỗi tối tích lũy (vào − ra) phải về ~0.

### 3.4 Đưa vào mô hình (digital shadow)
`sensor CSV → simulation/inputs.py (đọc, gộp 15 phút) → hiệu chỉnh λ(t), thời lượng phục vụ → chạy ABM lại mỗi đêm → dashboard so sánh "mô hình vs đo".`

---

## 4. Ý tưởng 3: Khảo sát hành vi thật, có quà tặng (merch, kẹo, đồ ngọt)

### 4.1 Yêu cầu thiết kế
- **Ngắn:** 5–7 phút. Phần bản đồ hành trình ~2 phút.
- **Đồng thuận:** câu xin phép ở đầu phiếu, nêu mục đích nghiên cứu, ẩn danh, quyền dừng bất kỳ lúc nào.
  **Quà tặng phát cho mọi người tham gia, không phụ thuộc câu trả lời.**
- **Phê duyệt đạo đức** của trường (IRB) và thư đồng ý của đơn vị vận hành khu nghỉ.
- **Ngôn ngữ:** Việt + Anh; cân nhắc Hàn, Trung, Nga theo cơ cấu khách thực tế.
- **Lấy mẫu phân tầng:**
  - địa điểm: sảnh khách sạn buổi tối, cổng ra công viên, khu nghỉ;
  - khung giờ;
  - loại nhóm.
- **Cỡ mẫu:**
  - ≈ 385 phiếu cho tỷ lệ ±5% (95%);
  - ≥ 100–150 phiếu mỗi phân khúc nếu so sánh phân khúc;
  - thí nghiệm lựa chọn (§4.3 khối G) thường cần vài trăm người × 6–8 tình huống.
- **Câu hỏi trung lập**, không dẫn dắt, mỗi câu một ý, dùng thang Likert 5 mức.
- **Thử nghiệm trước** (pilot) với 20–30 khách, sửa câu khó hiểu.
- **Ghi kèm:** thời điểm, địa điểm phỏng vấn, mã điều tra viên, để kiểm soát sai lệch.
- **Sai lệch cần nêu trong bài:**
  - nhớ lại (giờ thường làm tròn 30 phút);
  - mong muốn xã hội (khách nói "xanh" hơn thực tế);
  - tự chọn vì quà (người thích quà trả lời nhiều hơn);
  - ngôn ngữ.

### 4.2 Bộ câu hỏi theo khối, kèm câu trả lời khách thường đưa ra

> Cột "Câu trả lời thường gặp" là **giả thuyết để thiết kế lựa chọn đáp án và kiểm tra pilot**, không phải kết quả.

| Khối | Câu hỏi (ví dụ) | Dạng | Câu trả lời thường gặp / lưu ý | Tham số mô hình |
|---|---|---|---|---|
| A. Hồ sơ nhóm | Nhóm mấy người? Trẻ em dưới 8 tuổi? Người lớn tuổi? Ở mấy đêm? Lần đầu đến? Vé combo KS+công viên? | chọn | Gia đình 3–4 người có trẻ nhỏ, cặp đôi; nhiều người lần đầu; nhiều vé combo | thành phần nhóm, A05–A07 |
| B. Hành trình hôm nay | Trên bản đồ, đánh số thứ tự các khu đã đi và giờ ước lượng | bản đồ in sẵn | Nhớ được 4–8 điểm; giờ làm tròn; hay quên điểm ngắn (WC, mua nước) | **chuỗi Markov**, kiểm định quỹ đạo |
| C. Lý do chọn điểm tiếp theo | Vì sao chọn khu kế tiếp? (chọn tối đa 2) | chọn nhiều | "Gần chỗ đang đứng", "con thích", "thấy ít người xếp hàng", "theo giờ diễn", "trời nóng nên vào trong nhà", "nhân viên/app gợi ý" | trọng số travel/wait/pref/heat |
| D. Chờ đợi | Lâu nhất chịu chờ cho trò yêu thích / trò bình thường? Có bỏ hàng lần nào không, vì sao? Hôm nay chờ lâu nhất bao nhiêu phút? | số + chọn | Chịu chờ trò bình thường ~15–30 phút, trò "đinh" lâu hơn; bỏ hàng do trẻ mệt, nóng, sắp tới giờ diễn; **thời gian chờ cảm nhận thường dài hơn thực tế** | `queue_tolerance`, `abandon_probability` |
| E. Nhu cầu | Ăn trưa lúc mấy giờ, ở đâu? Mức nóng/mệt lúc 12h, 15h (1–5)? Nghỉ ở đâu? | số + Likert | Ăn trưa khoảng 11:30–13:00; nóng nhất buổi trưa–đầu chiều; khu có máy lạnh được ưa thích | tốc độ đói/mệt/nóng |
| F. Phòng khách sạn | Thường đặt điều hòa bao nhiêu độ? Ra ngoài có để máy lạnh chạy (cài thẻ phụ)? Có muốn phòng được làm mát sẵn trước khi về? Dùng lại khăn không? Treo DND? | chọn | Nhiều người chọn 24–25 °C, một phần 22–23 °C; một số để máy chạy "để về phòng mát": đây là cơ sở cho chính sách làm mát trước; chấp nhận dùng lại khăn khá cao nhưng không phải tất cả | `guest_setpoints`, `bypass_share`, `towel_request` |
| G. Chấp nhận gợi ý (thí nghiệm lựa chọn) | "Trò bạn định chơi đang chờ 40 phút. App gợi ý trò B chờ 10 phút, đi bộ thêm 6 phút, bạn thích B mức 3/5. Bạn sẽ: giữ trò cũ / đổi sang B?" (6–8 tình huống thay đổi thuộc tính) | lựa chọn rời rạc | Đổi khi tiết kiệm đủ lớn và không xa; gia đình có trẻ nhỏ nhạy với quãng đi bộ | **ước lượng trực tiếp trọng số hữu dụng** (logit) |
| H. Thái độ xanh | Mức đồng ý: "Tôi sẵn sàng dùng lại khăn để tiết kiệm năng lượng", "Tôi chấp nhận phòng ấm hơn 1–2 °C khi vắng mặt", "Tôi thoải mái nếu khu nghỉ dùng cảm biến đếm người (không lưu hình)" | Likert 5 | Đồng ý cao ở câu khăn (mong muốn xã hội); thấp hơn ở câu nhiệt độ; quyền riêng tư tùy quốc tịch | `conservation_pref`, `rec_acceptance` |
| I. Mở | Điều gì làm hôm nay khó chịu nhất? | tự do | Nắng nóng, xếp hàng, thiếu bóng mát, di chuyển giữa khu | gợi ý cơ chế mới |

### 4.3 Thí nghiệm lựa chọn (khối G): khối quan trọng nhất cho bài báo
- Thiết kế D-efficient với các thuộc tính:
  - thời gian chờ (10/25/40 phút);
  - đi bộ thêm (2/6/12 phút);
  - mức thích (1–5);
  - trong nhà/ngoài trời;
  - có giờ diễn sắp tới hay không.
- Ước lượng bằng **multinomial logit / mixed logit**. Hệ số thu được thay trực tiếp cho `decision.weights` và nhiệt độ
  softmax trong `config/defaults.json`.
- Khi đó mô hình quyết định trong ABM có **cơ sở thực nghiệm**. Đây là điểm reviewer đánh giá cao nhất.

---

## 5. Ánh xạ dữ liệu → tham số → kiểm định

| Tham số trong repo | Nguồn hiệu chỉnh | Dữ liệu kiểm định (giữ lại) |
|---|---|---|
| `population.*` (nhóm, giờ dậy/đi/về) | PMS + log thẻ từ + khảo sát A, F | cảm biến cổng phía KS |
| `decision.weights`, `temperature` | thí nghiệm lựa chọn G + chuỗi B | chuỗi hành trình ngày giữ lại, Markov baseline |
| `queue_tolerance`, `abandon_probability` | khảo sát D | đếm vào/ra hàng chờ (Little) |
| `facilities.*` (sức chứa, chu kỳ, giờ) | log điều phối trò, giờ mở cửa | thời gian chờ đo được |
| `rooms.*` (C, UA, COP, setpoint, bypass) | BMS phòng | kWh điều hòa theo tầng/khối |
| `hotel_common`, `restaurant`, `laundry` | công tơ bộ phận | công tơ bộ phận ngày giữ lại |
| `emissions.factor` | hệ số lưới chính thức | (không cần kiểm định) |

Tiêu chí kiểm định nên báo cáo:
- RMSE / MAPE điện 15 phút theo mục đích;
- sai số thời gian chờ trung bình & P90 theo giờ;
- khoảng cách phân bố giờ về khách sạn (KS-statistic);
- log-likelihood chuỗi điểm đến (ABM vs Markov).

---

## 6. Lộ trình gợi ý

1. **Pha 0 (đã xong):** ABM synthetic + dashboard + bản đồ 3D, kiểm tra mô hình tự động.
2. **Pha 1: Thỏa thuận & pilot.** Thư hợp tác/NDA với đơn vị vận hành, IRB, xin dữ liệu lịch sử (PMS, công tơ, BMS, log trò
   chơi). Lắp 2–3 cảm biến thử, pilot khảo sát 20–30 phiếu.
3. **Pha 2: Thu thập.** Khảo sát chính (≥ 400 phiếu + thí nghiệm lựa chọn), cảm biến chạy liên tục ≥ 2–4 tuần, gồm cả
   ngày thường và cuối tuần.
4. **Pha 3: Hiệu chỉnh & kiểm định.** Pattern-oriented calibration trên tuần 1–2, kiểm định trên tuần 3–4. Phân tích độ
   nhạy và Markov baseline.
5. **Pha 4: Digital shadow → twin.** Pipeline tự động đọc cảm biến mỗi đêm. Thử một chính sách thật quy mô nhỏ (ví dụ
   gợi ý trò chơi qua bảng/app) và đo lại: đây là vòng phản hồi.

## 7. Cấu trúc bài báo đề xuất
1. Giới thiệu & câu hỏi nghiên cứu (theo đề cương dự án).
2. Tổng quan: ABM du lịch/công viên, năng lượng khách sạn, digital twin trong hospitality.
3. Mô hình (ODD+D).
4. Dữ liệu: khảo sát, cảm biến, dữ liệu vận hành; đạo đức & quyền riêng tư.
5. Hiệu chỉnh & kiểm định (kèm Markov baseline).
6. Thí nghiệm chính sách: so sánh ghép cặp nhiều seed, đánh đổi điện–tiện nghi–chờ đợi.
7. Thảo luận: giới hạn (ranh giới phát thải, hệ số trung bình, tính khái quát).
8. Kết luận.

**Đóng góp có thể tuyên bố:**
- (i) ABM liên kết khách sạn–công viên với hành vi nhóm khách và điện năng;
- (ii) trọng số lựa chọn từ thí nghiệm lựa chọn tại chỗ;
- (iii) so sánh ABM vs Markov trong dự báo dòng khách;
- (iv) nguyên mẫu digital shadow chi phí thấp bằng cảm biến USB.
