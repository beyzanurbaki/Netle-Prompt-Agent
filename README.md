# Netle

Netle, örnek şirket belgelerine göre standart iade talepleri için müşteri yanıtı taslağı hazırlar.

## Yerel arayüz

Ollama açıkken ve `qwen2.5:1.5b` ile `embeddinggemma:latest` modelleri yüklüyken proje klasöründe çalıştır:

```powershell
python -m pip install -r requirements.txt
python netle_web.py
```

Tarayıcıdan [Netle’yi aç](http://127.0.0.1:8000). Örneğin “Müşteri kahve makinesini iade etmek istiyor. Kutusu açılmış, ürün kullanılmamış.” yaz. Teslimattan geçen gün sayısını ve aksesuar durumunu aynı ekranda yanıtla. Taslağı düzenleyebilir, kullanılan kaynakları ve promptu ayrı bölümde görebilirsin.

Arayüz standart iade, hasarlı teslimat ve hijyen mühürlü ürün akışlarını destekler. Serbest metindeki açık ifadeler kullanılır, eksik bilgiler aynı ekranda sorulur. LangGraph oturumu ve model çağrısı ölçümleri yerel SQLite dosyasına kaydedilir.

- “Vazo kırık geldi. Sipariş numarası TEST-103.” isteğinde hasar incelemesine geçilir ve eksik fotoğraflar istenir.
- “Hijyen mühürlü havluyu iade etmek istiyor.” isteğinde önce mühür durumu sorulur. Mühür açılmamışsa standart iade koşulları değerlendirilir.

Belge vektörleri ilk aramada SQLite'a yazılır. Netle, kaynakların içeriği ve sürümü ile Ollama embedding modelinin kimliği aynı kaldığı sürece kaydedilen vektörleri kullanır. Her yeni isteğin sorgu vektörü ayrıca hesaplanır.

Pilot kuralları, incelenmiş belge bölümlerinin sürümlerine ve içerik özetlerine bağlıdır. Bir bölüm değişirse, gerekli kaynak bulunamazsa veya koleksiyona incelenmemiş bir bölüm eklenirse Netle taslağı durdurup kullanıcıyı uyarır. Yeni kaynaklar kullanılmadan önce hem kaynaklar hem de onlara dayanan iade kuralları yeniden incelenir.

**Prompt şablonları** bölümünde standart iade şablonunu kaynak sürümleriyle birlikte önizleyip onaylayabilirsin. Daha önce onaylanan şablonlar aynı SQLite dosyasından yüklenir. Ürün bilgilerini doldurup **Şablonla taslak oluştur** seçeneğini kullan. Kaynağı değişen şablonlar yeniden inceleme bekler. **Token ve süre ölçümleri** müşteri taslağından ayrı gösterilir.

[Örnek veriler ve terminal demoları](ornek-veri/README.md)
