# Netle

Netle, BTK Akademi Yapay Zekâ Kariyer Atölyesi için geliştirilen yerel bir prompt ajanı prototipidir. Müşteri hizmetleri çalışanının serbest metinle yazdığı iade isteğini netleştirir, ilgili şirket kurallarını bulur ve düzenlenebilir bir müşteri yanıtı taslağı hazırlar. Pilot, kurgusal Luma Ev belgeleriyle çalışır.

## Kurulum ve çalıştırma

Python 3.13 ve Ollama ile Windows üzerinde test edildi. Ollama çalışırken proje klasörünü PowerShell veya VS Code'da aç. Modeller henüz yüklü değilse ilk iki komutu çalıştır:

```powershell
ollama pull qwen2.5:1.5b
ollama pull embeddinggemma:latest
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe netle_web.py
```

[Netle’yi tarayıcıda aç](http://127.0.0.1:8000). VS Code'da terminal komutu yerine **Run and Debug → Netle’yi Başlat → ▶** seçeneğini veya **F5** tuşunu kullanabilirsin. Bu seçenek, Python ve Python Debugger eklentileriyle proje klasöründeki `.venv` ortamını kullanır.

İlk kullanımda **Kayıt ol** ile bir yerel çalışan hesabı oluştur. Sonraki kullanımlarda aynı hesapla **Oturum aç** seçeneğini kullan. Hesaplar ve oturumlar yerel SQLite dosyasında saklanır. Her çalışan kendi iade oturumunu sürdürebilir. Onaylı prompt şablonları uygulamadaki çalışanlara ortak sunulur. Bu kayıt ekranı yerel pilot içindir ve şirket kimlik sağlayıcısıyla bağlantı kurmaz.

## Demo

| İstek örneği | Beklenen davranış |
|---|---|
| “Müşteri kahve makinesini iade etmek istiyor. Kutusu açılmış, ürün kullanılmamış.” | Teslimattan geçen günü ve aksesuar durumunu sorar. 12 gün ve eksiksiz aksesuar yanıtlarıyla iade başvuru yolunu gösterir. |
| “Vazo kırık geldi. Sipariş numarası TEST-103.” | Sipariş numarasını tekrar sormadan eksik fotoğraf bilgilerini ister. Hasarlı teslimat incelemesine yönlendirir. |
| “Müşteri hijyen mühürlü havluyu iade etmek istiyor.” | Mührün açılıp açılmadığını sorar. Mühür açıksa standart iadenin neden yapılamadığını açıklar. Mühür sağlamsa standart koşulları değerlendirir. |
| “Kullanılmamış ve aksesuarları tam olan abajur için bugün 30. gün. İade yanıtı yaz.” | Bugünün son başvuru günü olduğunu ve başvuru yolunu belirtir. |

**Prompt ve kaynaklar** ile **Token ve süre ölçümleri** müşteri taslağından ayrı gösterilir. **Prompt şablonları** bölümünden standart iade, hasarlı teslimat veya hijyen mühürlü ürün şablonunu seçip önizleyebilir ve onaylayabilirsin. Onaylı şablonlar yeni ürünlerle tekrar kullanılabilir. Her şablonun onayı kendi kaynak sürümleriyle birlikte kaydedilir.

## Ders kavramları uygulamada

| Kavram | Netle’deki karşılığı |
|---|---|
| RAG ve embedding | Markdown politika bölümleri Ollama ile vektörlere dönüştürülür ve kosinüs benzerliğiyle sıralanır. Belge vektörleri, içerik veya embedding modeli değişene kadar SQLite'tan yeniden kullanılır. |
| Ajan akışı ve LangGraph | Açık ifadelerden iade türü seçilir. Eksik bilgi varsa akış durur ve kullanıcı yanıtıyla aynı oturumdan devam eder. |
| Prompt iyileştirme ve yerel LLM | İlgili politika, bilinen bilgiler ve beklenen karar bir görev promptuna dönüştürülür. Ollama müşteri mesajı üretir. |
| İnsan onayı ve ölçüm | Şablonlar kullanıcı onayıyla kaydedilir. Girdi ve çıktı tokenları ile çağrı süreleri SQLite'ta izlenir. |

## Pilot değerlendirmesi

Altı kurgusal senaryoda ilk istek, doğrudan RAG ve Netle aynı `qwen2.5:1.5b` modeli ve üretim ayarlarıyla çalıştırıldı. Doğrudan RAG ve Netle aynı doğrulanmış bilgilere ve kaynaklara erişti. Taslaklar doğruluk, eksiksizlik ve biçime uyum açısından elle puanlandı.

| Yöntem | Kalite puanı /36 | Toplam üretim tokenı | Toplam üretim süresi |
|---|---:|---:|---:|
| İlk istek | 4 | 1.166 | 19,33 sn |
| Doğrudan RAG | 6 | 3.106 | 22,60 sn |
| Netle | 36 | 3.192 | 17,01 sn |

Bu tek ölçümde ortak sorgu embedding’leri ayrıca 450 token ve 12,65 saniye kullandı. Belge vektörleri önbellekten alındı. Netle altı senaryodan beşinde modelin ham çıktısı yerine incelenmiş müşteri mesajını gösterdi. Kalite puanı gösterilen taslağa, token ve süre ölçümleri gerçek model çağrısına ait.

Karşılaştırmayı yeniden çalıştırmak için:

```powershell
.\.venv\Scripts\python.exe compare_refund.py --all
```

Tek senaryo için `--scenario I04` kullanılabilir. Taslaklar `comparison_answers`, çağrı ölçümleri `model_calls` tablosuna yazılır. `netle.sqlite3` yerel kullanımda oluşur ve Git'e eklenmez.

## Pilotun sınırları

- Serbest metin yönlendirmesi açık Türkçe ifadelere dayanır. Bazı dolaylı anlatımlarda bilgi metinde yer alsa bile Netle tekrar sorabilir.
- İade akışları incelenmiş örnek şirket kurallarına bağlıdır. Gerekli kaynak eksikse, içerik aynı sürümde değişirse veya incelenmemiş bir bölüm eklenirse kullanıcı uyarılır ve taslak durdurulur.
- Sistem müşteri mesajı taslağı hazırlar. İade kaydı açma ve müşteriye mesaj gönderme işlemleri demo kapsamı dışındadır.

[Örnek belgeler, senaryolar ve terminal demoları](ornek-veri/README.md)
