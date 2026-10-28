# PDF Küçült

PDF'leri görüntü kalitesini koruyarak küçülten, tamamen bilgisayarınızda çalışan ücretsiz araç.
Dosyalar hiçbir sunucuya gönderilmez.

## İndir

**[En son sürüm](https://github.com/aykutpurde/pdf-kucult/releases/latest)** sayfasından:

- **Mac** (M serisi ve Intel): `PDF-Kucult-Mac.dmg`
- **Windows**: `PDF-Kucult-Kurulum.exe`
- **Zotero 7 eklentisi**: `pdf-kucult-zotero.xpi` (bir kez kurulduktan sonra kendiliğinden güncellenir)

Kurulum adımları `Kurulum-Rehberi.html` dosyasında. Uygulama Apple/Microsoft imzalı olmadığı için ilk açılışta
Mac'te "Yine de Aç", Windows'ta "Daha fazla bilgi › Yine de çalıştır" demeniz gerekir.

## Kullanım

**Uygulama:** `~/Applications/PDF Küçült.app` (Dock'a sürükleyin)

- PDF dosyalarını veya klasörleri simgenin üzerine bırakın ya da uygulamayı çift tıklayıp seçin.
- İşlem hemen başlar; ilerleme penceresini izleyin. "Durdur" düğmesiyle istediğiniz an iptal edebilirsiniz.
- Çıktıların konumu:
  - Bir **dosya** bıraktıysanız, aynı klasörde `ad (küçük).pdf` olarak.
  - Bir **klasör** bıraktıysanız, yanında `klasör (küçültülmüş)` adlı yeni bir klasörde, alt klasör yapısı aynen korunarak.
- **Orijinal dosyalara asla dokunulmaz.** Sonuçlardan memnunsanız orijinalleri kendiniz silebilirsiniz.
- Raporlar `~/Library/Logs/PDFKucult/` klasörüne kaydedilir. Her dosya için sayfa başına boyut da (önce → sonra) yazılır.

## Zotero eklentisi

**Kurulum:** Zotero › Tools › Plugins › ⚙ › *Install Plugin From File…* → `PDF Küçült Zotero.xpi`

**Kullanım:**
- **Seçili öğeler:** Öğelere sağ tıklayın › **Akıllı sıkıştır (PDF Küçült)**. Makale öğesi seçerseniz altındaki PDF ekleri işlenir.
- **Bütün kütüphane:** Tools › PDF Küçült › *Seçili kütüphanedeki tüm PDF'leri akıllı sıkıştır*. Onay ister; en büyük dosyalardan başlar.
- **Otomatik:** Tools › PDF Küçült › *Yeni eklenen PDF'leri otomatik akıllı sıkıştır*. "Find Available PDF" veya sürükle-bırak ile gelen her PDF eklendikten birkaç saniye sonra küçültülür. Varsayılan olarak açık.
- **KB/sayfa sütunu:** Öğe listesinde sütun başlığına sağ tıklayıp *KB/sayfa*'yı açın. Her PDF'in sayfa başına boyutu görünür; 150 KB'ın üstü ⚠ ile işaretlenir ve incelemeye değer (siyah-beyaz taramada normal 15–100 KB, dijital metinde 5–80 KB). Sütuna tıklayarak en şişkin dosyaları üste sıralayabilirsiniz. Sayfa sayısı Zotero'nun tam metin dizininden okunur; dizinlenmemiş PDF'lerde boş kalır.

**Nasıl çalışır:**
- Dosya yerinde değiştirilir. Zotero öğesi, notlar, PDF üzerindeki vurgular/açıklamalar ve etiketler aynen kalır.
- Büyük orijinal macOS **Çöp Kutusu'na** taşınır. Adı `… (orijinal, Zotero ANAHTAR).pdf` olur; oradan geri alınabilir.
- Dosya yeni tarihle yazılır, böylece Zotero senkronizasyonu küçük dosyayı buluta yükler.
- Daha önce küçültülen dosyalar tekrar işlenmez. Kayıt yeri: `~/Zotero/pdfkucult.json`
- İşlem kaydı Tools › PDF Küçült › *İşlem kaydını aç* ile görülebilir. Kayıt yeri: `~/Library/Logs/PDFKucult/zotero.log`

Eklenti motoru `~/Library/Application Support/PDFKucult/motor.py` konumundan çalıştırır; motoru `kur.sh` günceller.

## Akıllı sıkıştırma

Tek bir mod vardır: **akıllı sıkıştırma**. En çok küçültmeyi hedefler. Ölçüt: metin okunabilir kalsın, sayfa sayısı ve
künye belli olsun. Bunun için:

- **Taramalar:** Metin ağırlıklı taranmış sayfalar (fotoğraf makinesiyle çekilmiş gri sayfalar dahil)
  300 DPI siyah-beyaz JBIG2'ye çevrilir. Resimli tarama sayfaları da siyah-beyaz olur: yazılar
  keskin, resimler gazete baskısı gibi noktalı. 320 DPI'dan yüksek siyah-beyaz taramalar 300 DPI'ya iner.
- **Çift sayfa taramalar** (fotokopi, kitap tarayıcısı) kitap sırtından ikiye bölünür, siyah kenar
  bantları kırpılır; OCR katmanı ve yer imleri korunur. Not/vurgu içeren PDF'lerde sayfalar bölünmez.
- **Görseller gri olur:** fotoğraflar en çok 150 DPI / JPEG kalitesi 50; grafikler kayıpsız gri,
  450 DPI üstündekiler 300 DPI'ya iner. Baskı için CMYK görseller (JPEG 2000 dahil) dönüştürülür.
- **"Yazdır > PDF"** ile metni çizim olarak kaydedilmiş sayfalar siyah-beyaz taramaya çevrilip
  OCR ile aranabilir yapılır.
- **Dev baskı renk profilleri** (200 KB üstü CMYK) standart CMYK ile değiştirilir ya da yalnızca
  PDF/X baskı hedefiyse atılır.

> Görseller gri, taranmış sayfalar siyah-beyaz olur. Renk gerekiyorsa orijinali saklayın: uygulama orijinale
> hiç dokunmaz; Zotero eklentisi orijinali Çöp Kutusu'na taşır, oradan geri alınabilir.

Ayrıca, kayıpsız olarak:
- Gömülü fontlar yalnızca belgede kullanılan harflere indirilir. Word'den çıkan makalelerde en büyük kazanç buradan gelir.
  - Tüm TrueType fontlar küçültülür; ekran ipucu (hinting) verisi, PDF'te kullanılmayan dizgi tabloları ve gömülü bitmap'ler de atılır.
  - Harf numaraları korunduğu için sayfa içeriği, metin, bağlantılar ve etiketler değişmez.
- Tekrar eden fontlar ve görüntüler birleştirilir.
- Siyah-beyaz taramalar kayıpsız JBIG2'ye çevrilir. Tarama benekleri ve harf kenarındaki tek piksellik pürüzler silinir; noktalı resim sayfalarına dokunulmaz.
- iLovePDF/ABBYY gibi programların JPEG'in üstüne ikinci bir sıkıştırma eklediği görüntüler de işlenir.
- Sayfayı "piksel/72" boyutunda (55-90 cm) kaydeden tarama servislerinin dosyalarında çözünürlük A4 sayfa varsayılarak hesaplanır; aksi halde bu taramalar düşük çözünürlüklü sanılıp küçültülmüyordu.
- JPEG'ler mozjpeg ile kayıpsız yeniden paketlenir (~%5).
- Paletli (Indexed) ve JPEG 2000 fotoğraflar JPEG'e çevrilir; az renkli grafikler kayıpsız kalır.
- Çizim olarak kaydedilmiş metnin koordinatları gözle fark edilmeyecek hassasiyete (0,01) yuvarlanır; sayfa birebir kontrol edilir.
- Metni olmayan taranmış sayfalara OCR ile görünmez metin katmanı eklenir (Türkçe, İngilizce, Almanca, Fransızca).
- Dosya yapısı yeniden paketlenir.

## Güvenlik önlemleri

- **Font kontrolü:** Fontlar önce ayrı bir kopyada denenir. Font değişen sayfalar orijinalle piksel piksel aynı olmalı ve belgenin metni birebir korunmalıdır; aksi halde o fonta dokunulmaz.
- **Kalite kontrolü:** Her dosyada değişen sayfalar orijinalle karşılaştırılır (yüksek çözünürlükte render edilip ortalanarak; siyah-beyaz taramalarda yanlış alarm vermez). Sorunlu bir sayfa bulunursa o sayfadaki görüntüler hiç değiştirilmeden bırakılır.
- **Siyah-beyaz dönüşüm** yalnızca sayfayı kaplayan tarama görüntülerine uygulanır ve orijinale %95'ten az benziyorsa yapılmaz. Böylece kapaklar, fotoğraflar ve dijital kitaplardaki şekiller korunur.
- **JBIG2'nin güvenli modu kullanılır.** Agresif "sembol" modu benzer karakterleri birleştirebiliyor (6 ile 8'in karışması gibi); bu mod kullanılmaz.
- **Korunanlar:** metin ve OCR katmanı (arama, kopyalama), yer imleri, bağlantılar, notlar, dosya tarihleri.
- **E-imzalı ve parola korumalı PDF'lere dokunulmaz;** orijinal kopyalanır.
- Sonuç orijinalden büyükse orijinal kopyalanır.

## Komut satırı

```bash
~/Library/Application\ Support/PDFKucult/venv/bin/python motor.py ~/Kitaplar/
```

`--ocr-yok` metin tanımayı, `--bolme-yok` çift sayfa bölmeyi kapatır (sayfa numaraları değişmesin).
Test ve karşılaştırma için eski seviyeler hâlâ seçilebilir: `-s az|onerilen|asiri|okuma` (varsayılan `okuma`).

## Kurulum / güncelleme

```bash
./kur.sh
```

Gerekli Homebrew araçlarını (jbig2enc, jpeg-turbo, poppler) ve Python ortamını kurar, ardından uygulamayı derler.
Homebrew Python'u güncelledikten sonra uygulama çalışmazsa yeniden çalıştırın.

## Dosyalar

- `motor.py` — sıkıştırma motoru (pikepdf + Pillow + jbig2enc + fontTools)
- `uygulama.applescript` — sürükle-bırak uygulaması
- `kur.sh` — kurulum betiği
- `simge.py` — uygulama simgesi
- `zotero-eklenti/` — Zotero eklentisinin kaynağı (`kur.sh` bunu `PDF Küçült Zotero.xpi` olarak paketler)

## Paylaşım paketleri (Mac + Windows)

Arkadaşlara gönderilecek, hiçbir kurulum gerektirmeyen paketler GitHub Actions'ta derlenir. Depo gizli: `aykutpurde/pdf-kucult`.

- **Mac:** `PDF-Kucult-Mac.dmg`. İçinde hem M serisi hem Intel motoru gömülü uygulama, Zotero eklentisi ve rehber var.
- **Windows:** `PDF-Kucult-Kurulum.exe`. Yönetici yetkisi istemez. Masaüstüne simge ve sağ tık › Gönder menüsüne kısayol ekler; Zotero eklentisini de içerir.
- **Rehber:** `Kurulum Rehberi.html`, arkadaşlar için adım adım kurulum ve kullanım.

Her `git push` sonrasında derleme kendiliğinden çalışır. Hazır dosyalar, ilgili çalıştırmanın **PDF-Kucult-dagitim** çıktısındadır:

```bash
gh run download -R aykutpurde/pdf-kucult -n PDF-Kucult-dagitim
```

Paketleme dosyaları:
- `paketleme/mac_motor.sh` — Mac motoru (PyInstaller + `dylib_topla.py`)
- `paketleme/mac_uygulama.sh` — Mac uygulaması ve .dmg
- `paketleme/win_derle.ps1` ve `paketleme/kurulum.iss` — Windows uygulaması ve kurulum dosyası
- `testler/` — her platformda örnek PDF üretip motoru sınayan testler

## Lisans

MIT — bkz. [LICENSE](LICENSE). Paketlerdeki açık kaynak bileşenler ve lisansları: [UCUNCU-TARAF.md](UCUNCU-TARAF.md).
