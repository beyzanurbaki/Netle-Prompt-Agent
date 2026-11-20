# Netle

Netle, örnek şirket belgelerine göre standart iade talepleri için müşteri yanıtı taslağı hazırlar.

## Yerel arayüz

Ollama açıkken ve `qwen2.5:1.5b` ile `embeddinggemma:latest` modelleri yüklüyken proje klasöründe çalıştır:

```powershell
python -m pip install -r requirements.txt
python netle_web.py
```

Tarayıcıdan [Netle’yi aç](http://127.0.0.1:8000). Örneğin “Müşteri kahve makinesini iade etmek istiyor. Kutusu açılmış, ürün kullanılmamış.” yaz. Teslimattan geçen gün sayısını ve aksesuar durumunu aynı ekranda yanıtla. Taslağı düzenleyebilir, kullanılan kaynakları ve promptu ayrı bölümde görebilirsin.

Arayüz standart iade pilotu içindir. Serbest metindeki açık gün, kullanım ve aksesuar ifadelerini tanır. Bilgi eksikse veya tanınmıyorsa soru gösterir. LangGraph oturumu ve model çağrısı ölçümleri yerel SQLite dosyasına kaydedilir.

Belge vektörleri ilk aramada SQLite'a yazılır. Netle, kaynakların içeriği ve sürümü ile Ollama embedding modelinin kimliği aynı kaldığı sürece kaydedilen vektörleri kullanır. Her yeni isteğin sorgu vektörü ayrıca hesaplanır.

[Örnek veriler ve terminal demoları](ornek-veri/README.md)
