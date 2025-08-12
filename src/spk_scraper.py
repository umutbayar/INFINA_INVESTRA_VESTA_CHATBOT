import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import json
import PyPDF2
import io
from datetime import datetime
import time
import schedule


GEMINI_API_KEY = "AIzaSyDIWuas_B5IJEVELSvE7TT5cwhZUoLCRRA"

# SPK'nın haftalık bültenleri yayınladığı ana sayfa
SPK_URL = "https://spk.gov.tr/spk-bultenleri/2025-yili-spk-bultenleri"

# --- GEMINI MODELİNİ YAPILANDIRMA ---
if GEMINI_API_KEY and GEMINI_API_KEY != "BURAYA_GEMINI_API_ANAHTARINI_YAPIŞTIR":
    genai.configure(api_key=GEMINI_API_KEY)

def get_latest_bulletin_url():
    """SPK web sitesinden en son haftalık bültenin linkini bulur."""
    print("1. Adım: SPK web sitesine bağlanılıyor ve en son bülten linki aranıyor...")
    try:
        # SSL sertifika doğrulama hatasını atlamak için verify=False eklendi.
        response = requests.get(SPK_URL, verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # GÜNCELLENMİŞ MANTIK: Sayfadaki tüm linkleri ('a' etiketleri) bul.
        all_links = soup.find_all('a')
        print(f"   -> Sayfada toplam {len(all_links)} adet link bulundu.")
        
        bulletin_link_element = None
        # Bu linklerin içinde, metninde "bülten no" ifadesi geçen ilk linki ara.
        # Bu, "Geçmiş Yıllara Ait Bültenler" gibi diğer linkleri elememizi sağlar.
        for link in all_links:
            if link.has_attr('href') and 'bülten no' in link.text.lower():
                bulletin_link_element = link
                print(f"   -> Bültenle ilgili ilk link bulundu: '{link.text.strip()}'")
                break # İlk bulunanın en güncel olduğunu varsayarak döngüyü kır.

        if bulletin_link_element:
            url = bulletin_link_element['href']
            # Eğer link göreceli ise (örn: /data/bulten.pdf), tam adresi oluştur.
            if not url.startswith('http'):
                url = "https://spk.gov.tr" + url
            print(f"   -> En son bülten linki bulundu: {url}")
            return url
        else:
            print("   -> HATA: Bülten linki bulunamadı. SPK sitesinin yapısı değişmiş veya link metinleri 'Bülten No' ifadesini içermiyor olabilir.")
            return None
    except requests.exceptions.RequestException as e:
        print(f"   -> HATA: SPK web sitesine bağlanılamadı: {e}")
        return None

def extract_text_from_pdf(pdf_url):
    """Verilen bir PDF linkinden metin içeriğini çıkarır."""
    print("2. Adım: PDF içeriği çekiliyor ve metne dönüştürülüyor...")
    try:
        # Gerçek bir tarayıcı gibi görünmek için User-Agent başlığı ekliyoruz.
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(pdf_url, verify=False, headers=headers)
        response.raise_for_status()
        
        # GÜNCELLENDİ: İndirilen içeriğin gerçekten PDF olup olmadığını kontrol et.
        content_type = response.headers.get('Content-Type', '').lower()
        if 'application/pdf' not in content_type:
            print(f"   -> HATA: İndirilen dosya bir PDF değil. İçerik Türü: {content_type}")
            print("   -> Bulunan link, muhtemelen bültenin bulunduğu bir ara sayfadır.")
            return None

        # İndirilen PDF'i bir bellek içi dosya gibi oku
        pdf_file = io.BytesIO(response.content)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text()
        
        print(f"   -> PDF'ten {len(text)} karakter metin başarıyla çıkarıldı.")
        return text
    # PyPDF2'nin spesifik hatalarını yakalamak için
    except PyPDF2.errors.PdfReadError as e:
        print(f"   -> HATA: PDF dosyası bozuk veya okunamıyor. Hata: {e}")
        return None
    except Exception as e:
        print(f"   -> HATA: PDF içeriği okunurken genel bir sorun oluştu: {e}")
        return None

def summarize_text_with_gemini(text):
    """Verilen metni Gemini LLM kullanarak özetler."""
    # DÜZELTME: API anahtarı kontrolü güncellendi.
    if not GEMINI_API_KEY or GEMINI_API_KEY == "BURAYA_GEMINI_API_ANAHTARINI_YAPIŞTIR":
        print("   -> HATA: Gemini API anahtarı ayarlanmamış. Özetleme yapılamıyor.")
        return "Gemini API anahtarı ayarlanmadığı için özet oluşturulamadı."

    print("3. Adım: Ham metin özetlenmek üzere Gemini'ye gönderiliyor...")
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = (
            "Sen bir finansal analiz asistanısın. Aşağıdaki SPK haftalık bülten metnini, bir banka portföy yöneticisinin "
            "bilmesi gereken en önemli noktaları vurgulayarak özetle. Özetini madde madde yap. Sadece şu konulara odaklan: "
            "Yeni halka arz onayları, mevcut şirketlerin sermaye artırımı veya azaltımı kararları, idari para cezası alan kurumlar "
            "ve piyasayı etkileyebilecek yeni düzenlemeler."
            f"\n\n--- BÜLTEN METNİ ---\n{text[:8000]}\n--- BÜLTEN METNİ SONU ---" # Metnin çok uzun olma ihtimaline karşı ilk 8000 karakteri gönder
        )
        
        response = model.generate_content(prompt)
        print("   -> Gemini'den özet başarıyla alındı.")
        return response.text
    except Exception as e:
        print(f"   -> HATA: Gemini API hatası: {e}")
        return "Özet oluşturulurken bir hata oluştu."

def save_summary_to_json(summary):
    """Oluşturulan özeti spk_ozetleri.json dosyasına kaydeder."""
    print("4. Adım: Oluşturulan özet 'spk_ozetleri.json' dosyasına kaydediliyor...")
    try:
        today_date = datetime.now().strftime("%d-%m-%Y")
        new_data = {
            "son_bulten": {
                "tarih": today_date,
                "bulten_no": f"{datetime.now().isocalendar()[0]}/{datetime.now().isocalendar()[1]}", # Yıl/Hafta No
                "ozet": summary
            }
        }
        with open('spk_ozetleri.json', 'w', encoding='utf-8') as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
        print("   -> Özet başarıyla kaydedildi!")
    except Exception as e:
        print(f"   -> HATA: JSON dosyasına yazılırken bir sorun oluştu: {e}")
def run_spk_update_job():
    """Tüm SPK bülten çekme ve özetleme adımlarını çalıştıran ana görev fonksiyonu."""
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Haftalık SPK Bülten Güncelleme Görevi Başlatıldı...")
    bulletin_url = get_latest_bulletin_url()
    if not bulletin_url:
        print("İşlem sonlandırıldı: En son bülten linki bulunamadı.")
        return

    raw_text = extract_text_from_pdf(bulletin_url)
    if not raw_text:
        print("İşlem sonlandırıldı: Bülten içeriği (PDF) okunamadı.")
        return

    summary_text = summarize_text_with_gemini(raw_text)
    if "hata" in summary_text.lower() or "oluşturulamadı" in summary_text.lower():
         print(f"İşlem sonlandırıldı: Gemini'den özet alınırken bir sorun oluştu.")
         return

    if save_summary_to_json(summary_text):
        print("SPK bülten özeti başarıyla güncellendi.")
    else:
        print("İşlem sonlandırıldı: Özet dosyaya kaydedilemedi.")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Görev tamamlandı. Bir sonraki Cuma'ya kadar bekleniyor...")


# --- Ana İş Akışı ve Zamanlayıcı ---
if __name__ == "__main__":
    print("--- SPK Bülten Otomatik Güncelleyici Başlatıldı ---")
    print("Bu script, Vesta'nın SPK özetlerini güncel tutmak için sürekli çalışacaktır.")
    

    schedule.every().friday.at("18:00").do(run_spk_update_job)
    
    print(f"Zamanlama ayarlandı: Görev her Cuma 18:00'de çalışacak.")
    print("İlk çalıştırma anında test için görev bir kez çalıştırılıyor...")
    run_spk_update_job() # Program başlarken bir kereliğine çalıştır

    while True:
        schedule.run_pending()
        time.sleep(600) 
    bulletin_url = get_latest_bulletin_url()
    
    if bulletin_url:
        raw_text = extract_text_from_pdf(bulletin_url)
        
        if raw_text:
            summary_text = summarize_text_with_gemini(raw_text)
            
            if summary_text:
                save_summary_to_json(summary_text)
                
    print("\nScript tamamlandı.")
