# Netle ilk deneme verisi

Luma Ev, kuralları ve müşteri talepleri bu demo için kurgulandı. Takvim günü sınırı ve ürün koşulları örnek politika verisidir.

- `belgeler/` RAG indeksinin kaynak klasörüdür.
- `senaryolar.json` ilk istekleri, netleştirme sırasında verilebilecek ek bilgileri ve değerlendirme referanslarını içerir. Test dosyasını indeksin dışında tut. `referans` alanı yalnızca değerlendirme için kullanılır.

İlk demo için I01'i kullan. Netle'ye `ilk_istek` ve `ilk_bilgiler` ile başla. Sorarsa teslimattan geçen gün, kullanım ve aksesuar bilgisini `ek_bilgiler` alanından ver. I04 ve I06 ek soru gerektirmeyen sınır vakalarıdır. I05'te teslimat günü bilinmiyor. Bu durumda iade uygunluğuna karar vermeden bilgi istenmesi beklenir.

Prompt iyileştirmeyi karşılaştırmak için doğrudan RAG'e ve Netle'ye aynı tamamlanmış görev bilgisini ve aynı belge kümesini ver. Model ve üretim ayarlarını sabit tut. Netleştirme katkısını ölçmek için aynı yöntemin ilk bilgilerle ve ek bilgilerle ürettiği yanıtları ayrıca karşılaştır. Dokümansız başlangıç denemesinde yalnızca ilk isteği ve ilk bilgileri kullan.

Her taslağı uygunluk kararı, gereken sonraki adım, politika dayanağı ve müşteri diline uygunluk açısından referansla karşılaştır. Referans taslağın kelimesi kelimesine tekrarlanması gerekmez.

## Belge aramasını çalıştırma

Ollama açıkken ve `embeddinggemma:latest` yüklüyken proje kökünde çalıştır:

```powershell
python rag.py --query "Lamba kutusu açılmış. Standart iade koşulları nelerdir?"
python rag.py --evaluate --top-k 3
python rag.py --evaluate --clarified --top-k 3
```

Arama, her Markdown politika bölümünü bir parça olarak işler ve sonuçları kosinüs benzerliğine göre sıralar. Belge vektörleri SQLite'tan yeniden kullanılabilir. Kaynaklar veya embedding modeli değiştiğinde indeks yeniden oluşturulur. Değerlendirme, beklenen kaynakların ne kadarının ilk sonuçlarda bulunduğunu ölçer. Ollama'ya giden indeksleme ve sorgu çağrıları SQLite'ta `embed_index` ve `embed_query` olarak ayrı kaydedilir.

## LangGraph ile lamba iadesi

Ollama'da `embeddinggemma:latest` ve `qwen2.5:1.5b` yüklüyken proje kökünde çalıştır:

```powershell
python -m pip install -r requirements.txt
python refund_graph.py start
python refund_graph.py resume OTURUM_KIMLIGI --days 12 --used no --accessories yes
```

İlk komutun ardından `start` kaynakları bulur, eksik bilgileri gösterir ve oturumu SQLite'a kaydeder. `resume` komutundaki `OTURUM_KIMLIGI` yerine ekrandaki kimliği yaz. Örnekteki yanıt değerleri I01'in ek bilgilerine karşılık gelir. Aynı veritabanını kullanarak yeni bir terminalden de devam edebilirsin. Akış I01 ve örnek belgelerin 1.0 sürümü için hazırlanmıştır.

## I01 prompt karşılaştırması

```powershell
python compare_refund.py
```

Üç taslak aynı üretim modeli ve ayarlarla üretilir. İlk istek tek başına çalışır. Doğrudan RAG ve Netle aynı doğrulanmış görev bilgilerini ve aynı kaynak parçalarını alır. Netle, LangGraph pilotundaki kurallı promptu ve içindeki örnek mesajı kullanır. Ortak embedding işlemleri bir kez çalışır ve üretim çağrılarından ayrı kaydedilir.

Her yanıtı üç ölçütte 0–2 arasında puanla (0 karşılamıyor, 1 kısmen, 2 karşılıyor).

- **Doğruluk** için iade kararını, kutu koşulunu ve mevcut bilgilerle kurulabilen iddiaları kontrol et.
- **Eksiksizlik** için uygunluk kararını ve müşterinin uygulayacağı sonraki adımı kontrol et.
- **Biçim** için kısa, nazik ve müşteriye doğrudan hitap eden Türkçe bir taslak olup olmadığına bak.
