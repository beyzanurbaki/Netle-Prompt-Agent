# NETLE

NETLE, BTK Akademi Yapay Zekâ Kariyer Atölyesi kapsamında geliştirilen yerel bir müşteri hizmetleri prototipidir. Çalışanın isteğini netleştirir, yönetici onaylı şirket belgelerinden ilgili kuralları bulur ve bu bilgileri görev promptuna veya müşteri yanıtı taslağına dönüştürür.

| Ekran | İş akışı |
|---|---|
| **İade Asistanı** | Gmail iade kutusundan vaka açar, sisteme aktarılmış sipariş bilgilerini kontrol eder, eksik bilgileri sorar ve düzenlenebilir bir müşteri yanıtı taslağı hazırlar. SMTP yapılandırılmışsa çalışan taslağı inceleyip gönderebilir. |
| **Prompt Geliştirici** | Serbest metinle yazılan müşteri hizmetleri isteğini netleştirir. Geliştirilmiş promptu, değişikliklerin gerekçelerini ve belge sürümlerini gösterir. Çalışan promptu kopyalayabilir ve isteğe bağlı olarak yanıt taslağını önizleyebilir. |

Yönetici çalışan hesaplarını, sipariş CSV aktarımlarını ve şirket belgelerini yönetir. Yüklenen PDF, DOCX, TXT ve Markdown belgeleri yönetici onayından sonra aramada kullanılır. Yeni sürüm onaylandığında önceki sürüm SQLite’ta arşivlenir. Metin katmanı içeren PDF’ler desteklenir.

## Kurulum

Windows üzerinde Python 3.13 ve Ollama ile çalıştırılabilir. Ollama çalışırken proje klasöründe şu komutları kullan.

```powershell
ollama pull qwen2.5:1.5b
ollama pull embeddinggemma:latest
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Proje kökünde yerel bir `.env` dosyası oluştur. Örnek kullanıcı adını değiştirebilir ve kendi yönetici parolanı girebilirsin.

```dotenv
NETLE_ADMIN_USERNAME=ornek.yonetici
NETLE_ADMIN_PASSWORD=
```

```powershell
.\.venv\Scripts\python.exe netle_web.py
```

[NETLE’yi aç](http://127.0.0.1:8000). VS Code ile çalışıyorsan **Run and Debug → Netle’yi Başlat** seçeneğini de kullanabilirsin.

## İlk deneme · Prompt Geliştirici

1. Yönetici hesabıyla giriş yap. **Çalışan ekle** bölümünde bir deneme hesabı oluştur. **Şirket belgeleri** bölümüne [kurgusal destek rehberini](ornek-veri/belgeler/destek-rehberi.md) yükle ve onayla.
2. Çalışan hesabıyla giriş yapıp **Prompt Geliştirici** ekranını aç. Örneğin “Müşteri kupasının kırık geldiğini ve bir fotoğraf gönderdiğini söylüyor. Hangi fotoğraf olduğu belirtilmemiş. Eksik belgeleri isteyen kısa bir müşteri yanıtı için prompt hazırla.” yaz.
3. Sorulan bilgiyi tamamla. Örneğin ürün fotoğrafının ulaştığını, dış ambalaj fotoğrafının eksik olduğunu ve sipariş numarasının bilindiğini belirt. Oluşturulan promptu, gerekçeleri ve belge sürümünü incele. İstersen **Yanıt taslağını da önizle** seçeneğini kullan.

Bu akış Gmail veya SMTP kurulumu gerektirmez.

### Canlı pilot gözlemi

Gerçek Ollama modelleriyle, kişisel veri içermeyen ve ayrı bir test rehberine dayanan kırık kupa vakası denendi. Test sırasında modelin yanıtlanmış soruyu tekrarladığı görüldü. Düzeltmeden sonra akış hangi fotoğrafın ulaştığını sordu ve yanıtı onaylı rehberle birleştirerek kaynaklı bir prompt üretti. Önizleme ambalaj ve kargo etiketi fotoğraflarıyla birlikte daha önce ulaşan ürün fotoğrafını da yeniden istedi. Bu vaka, önizleme metninin çalışan tarafından kontrol edilmesi gerektiğini gösterdi.

## İade Asistanı bağlantıları

### Gmail gelen kutusu

Deneme hesabında `Netle İade` etiketi oluştur ve bir test iletisine uygula. Gmail uygulama parolasını yerel `.env` dosyasına gir. `NETLE_GMAIL_EMPLOYEE`, gelen kutusunu kullanacak NETLE çalışan hesabıdır.

```dotenv
NETLE_GMAIL_ACCOUNT=
NETLE_GMAIL_APP_PASSWORD=
NETLE_GMAIL_EMPLOYEE=
```

NETLE etiketteki son 15 iletiyi okur. Sipariş kontrolünü denemek için yönetici panelinden `order_id,customer_email,product,purchase_date,delivery_date` başlıklarına sahip kurgusal bir CSV aktar.

### SMTP gönderimi

İade Asistanı’nda çalışan **Gönder** düğmesine bastığında düzenlenmiş taslak yapılandırılan SMTP sunucusu üzerinden iletilir. Aşağıdaki adresler örnek değerlerdir.

```dotenv
NETLE_SMTP_HOST=smtp.example.com
NETLE_SMTP_PORT=587
NETLE_SMTP_TLS=starttls
NETLE_SMTP_FROM=destek@example.com
NETLE_SMTP_USER=destek@example.com
NETLE_SMTP_PASSWORD=
```

Örtük TLS kullanılıyorsa `NETLE_SMTP_TLS=ssl` ve sunucunun belirttiği port kullanılır. Gönderimi kontrol ettiğin bir test hesabıyla dene.

## Teknik yapı ve değerlendirme

- **RAG.** Onaylı belgelerin ilgili bölümleri Ollama embedding modeliyle bulunur. Belge vektörleri yerel SQLite’ta yeniden kullanılabilir.
- **LangGraph.** İade akışı eksik bilgi olduğunda durur ve çalışanın cevabıyla aynı vakadan devam eder.
- **Ollama ve SQLite.** Model çağrılarının token ve süre bilgileri `model_calls` tablosunda tutulur. Uygulama hesapları, yüklenen belgeler ve sipariş kayıtları yerel veritabanında saklanır.

İlk istek, doğrudan RAG ve NETLE karşılaştırmasını kurgusal Luma Ev demo belgeleriyle çalıştırmak için:

```powershell
.\.venv\Scripts\python.exe compare_refund.py --all
```

Bu karşılaştırma, web panelinden yüklenen güncel pilot belgelerinden ayrı bir terminal demosudur. Senaryolar ve doğruluk, eksiksizlik, biçim rubriği [örnek veri rehberinde](ornek-veri/README.md) yer alır.

## Veri ve GitHub paylaşımı

Public depoda kurgusal belgeler, siparişler ve e-postalar kullan. Yerel `.env` dosyasını, SQLite veritabanını ve yan dosyalarını, müşteri e-postalarını, gerçek sipariş CSV’lerini, şirkete ait özel belgeleri ve bu verileri içeren ekran görüntülerini depoya ekleme. Yayından önce dosyaların yanı sıra commit geçmişindeki yazar, işleyen ve e-posta bilgilerini de kontrol et.
