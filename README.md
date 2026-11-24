# NETLE

NETLE, BTK Akademi Yapay Zekâ Kariyer Atölyesi kapsamında geliştirilen yerel bir müşteri hizmetleri prototipidir. Çalışanın sorusunu netleştirip onaylatır, yönetici onaylı şirket belgelerinden ilgili kuralları bulur ve şirket politikasına uygun bir müşteri yanıtı taslağı hazırlar.

| Ekran | İş akışı |
|---|---|
| **İade Asistanı** | Gmail iade kutusundan vaka açar, sisteme aktarılmış sipariş bilgilerini kontrol eder, eksik bilgileri sorar ve düzenlenebilir bir müşteri yanıtı taslağı hazırlar. SMTP yapılandırılmışsa çalışan taslağı inceleyip gönderebilir. |
| **Prompt Geliştirici** | Çalışan kısa veya eksik bir soru yazar. NETLE soruyu tam bir cümleye çevirip “Bunu mu demek istediniz?” diye sorar ve emin olamadığı noktaları belirtir. Çalışan “Evet” diyene kadar düzeltme yazabilir. Onaydan sonra onaylı şirket belgeleri taranır; NETLE kaynak numaralı bir politika açıklaması ve düzenlenebilir bir müşteri yanıtı taslağı hazırlar. Belgelerde dayanak yoksa yanıt üretmez. |

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
2. Çalışan hesabıyla giriş yapıp **Prompt Geliştirici** ekranını aç. Örneğin kısaca “kupa kırık gelmiş ne diyim” yaz.
3. NETLE’nin “Bunu mu demek istediniz?” yorumunu oku. Yanlış veya eksikse düzeltmeni yaz, örneğin “müşteri ürün fotoğrafını gönderdi”. Yorum doğruysa **Evet, doğru** düğmesine bas ya da “evet” yaz.
4. Politika açıklamasını, kaynak bölümlerini ve müşteri yanıtı taslağını incele. Taslağı düzenleyip kopyalayabilirsin.

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

### Değerlendirme modu

Değerlendirme komut satırından çalışır. Önce yönetici panelinde testte kullanılacak şirket belgelerini onayla. Hazır senaryolar için ayrı bir test veritabanında [Luma Ev iade politikasını](ornek-veri/belgeler/iade-politikasi.md) ve [destek rehberini](ornek-veri/belgeler/destek-rehberi.md) onayla. Başka bir belge kümesi kullanıyorsan senaryo ve referansları o kümeye göre önceden hazırla.

```powershell
.\.venv\Scripts\python.exe evaluate_support.py run --db netle.sqlite3 --cases ornek-veri/degerlendirme-senaryolari.json --output evaluation-results/pilot.json --with-ablation
.\.venv\Scripts\python.exe evaluate_support.py review evaluation-results/pilot.json
.\.venv\Scripts\python.exe evaluate_support.py summary evaluation-results/pilot.json
```

- **İlk istek** yalnızca başlangıç isteğini ve başlangıçta bilinen bilgileri kullanır. **Doğrudan RAG** ve **NETLE** aynı tamamlanmış görev bilgilerini, aynı onaylı belge kümesini ve aynı taslak üretim modeli ile ayarlarını kullanır. Her yöntem ilgili pasajları kendi arama adımıyla seçer.
- **Netleştirme katkısı.** `--with-ablation`, NETLE’yi başlangıç bilgileriyle bir kez daha çalıştırır. Taslak yerine soru veya uyarı alınırsa raporda durum görünür. `--case P01` ile tek senaryo seçilebilir.
- **Kalite ve düzenleme.** `review`, yöntem adlarını göstermeden taslakları karışık sırada sunar. Değerlendirici, referansa göre doğruluk, eksiksizlik ve biçimi 0–2 arasında puanlar. Düzenleme süresi taslak gösterildikten bitiş satırı girilene kadar ölçülür. Anlamlı düzeltme sayısını değerlendirici girer.
- **Kullanım ve maliyet.** Rapor her model çağrısının ve yöntemin toplam token, süre ve tahmini maliyetini, tekrar model çağrılarını ve eksik kullanım verisini kaydeder. Her vaka ve yöntem için arama indeksi soğuk başlar ve indeksleme maliyete dahil edilir. Yerel Ollama için varsayılan model faturası sıfırdır. Bir fiyat senaryosu için milyon token başına `--input-price`, `--output-price`, `--embedding-price` ve `--currency` girilebilir. Yerel donanım ve elektrik giderleri bu hesaba dahil değildir.

Referanslar değerlendirme sırasında kullanılır. Taslak üretimine gönderilmez. Raporlar taslakları ve belge pasajlarını içerebilir. `evaluation-results/` Git dışında tutulur.

Pilot öncesinde kullanılacak senaryoları ve kabul eşiklerini belirle. Özet, doğrudan RAG ile NETLE arasındaki farkı ortak vakalarda gösterir. NETLE’nin ek bilgili ve ek bilgisiz çalıştırmaları da aynı şekilde karşılaştırılır. Üretim süresi, çalışanın netleştirme sorularını cevaplamasını bekleme süresini içermez.

### Önceki terminal demosu

Eski Luma Ev karşılaştırmasını çalıştırmak için:

```powershell
.\.venv\Scripts\python.exe compare_refund.py --all
```

Bu karşılaştırma, web panelinden yüklenen güncel pilot belgelerinden ayrı bir terminal demosudur. Senaryolar ve doğruluk, eksiksizlik, biçim rubriği [örnek veri rehberinde](ornek-veri/README.md) yer alır.

## Veri ve GitHub paylaşımı

Public depoda kurgusal belgeler, siparişler ve e-postalar kullan. Yerel `.env` dosyasını, SQLite veritabanını ve yan dosyalarını, müşteri e-postalarını, gerçek sipariş CSV’lerini, şirkete ait özel belgeleri ve bu verileri içeren ekran görüntülerini depoya ekleme. Yayından önce dosyaların yanı sıra commit geçmişindeki yazar, işleyen ve e-posta bilgilerini de kontrol et.
