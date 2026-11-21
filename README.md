# Netle

Netle, BTK Akademi Yapay Zekâ Kariyer Atölyesi için geliştirilen yerel bir prompt ajanı prototipidir. Müşteri hizmetleri çalışanının serbest metinle yazdığı iade isteğini netleştirir, yönetici tarafından onaylanmış şirket dosyalarında ilgili kuralları bulur ve düzenlenebilir bir müşteri yanıtı taslağı hazırlar. `ornek-veri` altındaki kurgusal Luma Ev belgeleri terminal demo ve karşılaştırma senaryolarında kullanılır.

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

`.env` dosyasında `NETLE_ADMIN_USERNAME` ve `NETLE_ADMIN_PASSWORD` alanlarını belirle. Uygulama açıldığında bu bilgilerle yönetici hesabı oluşturulur veya güncellenir; sonraki kayıtlar çalışan hesabı olur. `.env` Git'e eklenmez. Yönetici hesabı **Şirket belgeleri** bölümünden PDF, DOCX, TXT veya MD dosyalarını yükleyebilir. Dosyanın metni incelendikten ve yönetici onayladıktan sonra belge RAG aramasında etkinleşir. Aynı başlıkta yeni içerik yüklenirse eski ve yeni metin karşılaştırma için gösterilir; yeni sürüm ancak onayla etkinleşir. Önceki sürümler SQLite'ta arşivlenir.

Üretim arayüzündeki iade taslakları ve prompt şablonları yalnızca etkin, yönetici onaylı dosyalardan getirilen pasajları kullanır. Onaylı belge yoksa veya ilgili bilgi bulunamazsa taslak oluşturulmaz. Orijinal dosya, çıkarılan metin ve sürüm geçmişi yerel `netle.sqlite3` veritabanında saklanır. PDF metin içermelidir; taranmış belge için OCR henüz desteklenmiyor. İlk dosya yüklemesinden önce `pip install -r requirements.txt` komutuyla PDF kitaplığı kurulur.

Hesaplar, roller ve oturumlar SQLite'ta saklanır. Yerel pilotun kayıt ekranı şirket kimlik sağlayıcısıyla bağlantı kurmaz.

Kullanıcı adı unutulursa SQLite içindeki `employees` tablosundan görülebilir. `.env` ayarlıysa yönetici parolasını bu dosyada güncelleyip uygulamayı yeniden başlat. Yönetici, parolasını unutan çalışan için panelden geçici parola oluşturabilir.

## Demo

| İstek örneği | Beklenen davranış |
|---|---|
| “Müşteri kahve makinesini iade etmek istiyor.” | İlgili onaylı belgelerdeki kurallara göre eksik bilgileri sorar ve taslak hazırlar. |
| “Vazo kırık geldi.” | Yalnızca onaylı belgelerde dayanağı varsa hasarlı teslimat için taslak üretir. |
| “İlgili bir iade kuralı bulunmayan ürün.” | Kaynak eşleşmesi yeterli değilse taslak oluşturmaz. |

**Prompt ve kaynaklar** ile **Token ve süre ölçümleri** müşteri taslağından ayrı gösterilir. **Prompt şablonları** bölümünden standart iade, hasarlı teslimat veya hijyen mühürlü ürün şablonunu seçip önizleyebilir ve onaylayabilirsin. Onaylı şablonlar yeni ürünlerle tekrar kullanılabilir. Her şablonun onayı kendi kaynak sürümleriyle birlikte kaydedilir.

## Ders kavramları uygulamada

| Kavram | Netle’deki karşılığı |
|---|---|
| RAG ve embedding | Yönetici onaylı dosyaların metin bölümleri Ollama ile vektörlere dönüştürülür ve kosinüs benzerliğiyle sıralanır. Belge vektörleri, içerik veya embedding modeli değişene kadar SQLite'tan yeniden kullanılır. |
| Ajan akışı ve LangGraph | Açık ifadelerden iade türü seçilir. Eksik bilgi varsa akış durur ve kullanıcı yanıtıyla aynı oturumdan devam eder. |
| Prompt iyileştirme ve yerel LLM | İlgili politika, bilinen bilgiler ve beklenen karar bir görev promptuna dönüştürülür. Ollama müşteri mesajı üretir. |
| İnsan onayı ve ölçüm | Şablonlar kullanıcı onayıyla kaydedilir. Girdi ve çıktı tokenları ile çağrı süreleri SQLite'ta izlenir. |

## Önceki demo ölçümü

Bu tablo yönetici belge paneli eklenmeden önce kurgusal Luma Ev belgeleriyle yapılan tek seferlik demo ölçümüdür. Güncel arayüzün yüklenen şirket belgeleriyle alacağı sonuçları göstermez. `compare_refund.py` karşılaştırma betiği de aynı eski demo korpusunu kullanır.

| Yöntem | Kalite puanı /36 | Toplam üretim tokenı | Toplam üretim süresi |
|---|---:|---:|---:|
| İlk istek | 4 | 1.166 | 19,33 sn |
| Doğrudan RAG | 6 | 3.106 | 22,60 sn |
| Netle | 36 | 3.192 | 17,01 sn |

Bu eski ölçümde ortak sorgu embedding’leri ayrıca 450 token ve 12,65 saniye kullandı. Yeni belgelerde gerçek kalite ve hız ölçümü ayrıca yapılmalıdır.

Karşılaştırmayı yeniden çalıştırmak için:

```powershell
.\.venv\Scripts\python.exe compare_refund.py --all
```

Tek senaryo için `--scenario I04` kullanılabilir. Taslaklar `comparison_answers`, çağrı ölçümleri `model_calls` tablosuna yazılır. `netle.sqlite3` yerel kullanımda oluşur ve Git'e eklenmez.

## Pilotun sınırları

- Serbest metin yönlendirmesi açık Türkçe ifadelere dayanır. Bazı dolaylı anlatımlarda bilgi metinde yer alsa bile Netle tekrar sorabilir.
- Dosyadan metin çıkarımı PDF, DOCX, TXT ve MD ile sınırlıdır. Taranmış PDF'lerde OCR yapılmaz.
- Kaynak dayanağını model yanıtından doğrulamak için kurallar eklenmiştir; yerel dil modelinin hatalı çıkarım yapma olasılığı tamamen ortadan kalkmaz. Yönetici yüklenen belge metnini ve sürüm değişikliklerini onaylamalıdır.
- Sistem müşteri mesajı taslağı hazırlar. İade kaydı açma ve müşteriye mesaj gönderme işlemleri demo kapsamı dışındadır.

[Örnek belgeler, senaryolar ve terminal demoları](ornek-veri/README.md)
