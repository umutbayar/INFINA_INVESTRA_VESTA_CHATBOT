#Libraries
import flask
from flask import session, redirect, url_for, send_from_directory
import json
import pyodbc
import os
import uuid
import google.generativeai as genai 
from datetime import datetime, timedelta
from google.cloud import dialogflow
from flask_cors import CORS
from dotenv import load_dotenv

# SQLAlchemy ve LangChain için gerekli kütüphaneler
from sqlalchemy import create_engine
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.agent_toolkits import create_sql_agent
from langchain.agents import AgentType
from langchain_community.utilities import SQLDatabase # DÜZELTME: Bu satır eklendi

# --- Proje Ayarları ---
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "dialogflow-credentials.json"
DIALOGFLOW_PROJECT_ID = "vesta-tyfm" # Dialogflow Ajan Ayarlarından Project ID'ni kontrol et
DIALOGFLOW_LANGUAGE_CODE = "tr"

# --- Hassas Bilgiler Ortam Değişkenlerinden Okunuyor ---
GEMINI_API_KEY = "AIzaSyB9a8qjLVBWREQDtmr7hWYVpLkczuPdcfE"

#GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

DB_SERVER = '192.168.144.76,1450' # Örn: '192.168.1.100' veya 'SERVER_ADI\SQLEXPRESS'
DB_DATABASE = 'Kizikos' # Örn: 'InvestraDB'
DB_USERNAME = 'grup4'
DB_PASSWORD = '8B8bToZc38mhQ'
DB_CONNECTION_STRING = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={DB_SERVER};DATABASE={DB_DATABASE};UID={DB_USERNAME};PWD={DB_PASSWORD}'

# --- Geliştirme Modu Anahtarı ---
GELISTIRME_MODU = False 

# Eğer GEMINI_API_KEY boşsa, Gemini API'si kullanılmayacak
load_dotenv()

def get_db_connection():
    """Veritabanına yeni bir bağlantı oluşturur."""
    try:
        conn = pyodbc.connect(DB_CONNECTION_STRING)
        # Bu print ifadesi artık her sorguda görünecek
        print("--- VERİTABANI BAĞLANTISI BAŞARIYLA SAĞLANDI ---")
        return conn
    except Exception as e:
        print(f"!!! VERİTABANI BAĞLANTI HATASI: {e} !!!")
        return None

# --- Önbellek Ayarları ---
stock_cache = {}
CACHE_SURESI_SANIYE = 60

app = flask.Flask(__name__)
CORS(app)

db = None
sql_agent = None
try:
    # 1. Veritabanı Bağlantısını LangChain için hazırla
    db_uri = (
        f"mssql+pyodbc://{DB_USERNAME}:{DB_PASSWORD}@{DB_SERVER}/{DB_DATABASE}?"
        "driver=ODBC+Driver+17+for+SQL+Server"
    )
    db_engine = create_engine(db_uri)
    
    # DÜZELTME: SQLAlchemy motorunu LangChain'in SQLDatabase nesnesine dönüştür
    db = SQLDatabase(engine=db_engine)

    # 2. LLM'i (Gemini) LangChain için hazırla
    llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", temperature=0, google_api_key=GEMINI_API_KEY)

    # 3. SQL Ajanını oluştur
    sql_agent = create_sql_agent(
        llm=llm,
        db=db, # DÜZELTME: Artık doğru nesne türü gönderiliyor
        agent_type="openai-tools",
        verbose=True,
        agent_executor_kwargs={"handle_parsing_errors": True}
    )
    print("--- LangChain SQL Ajanı başarıyla başlatıldı. ---")
except Exception as e:
    print(f"!!! LANGCHAIN BAŞLATMA HATASI: {e} !!!")

# Hangi rolün hangi intent'lere ve konulara erişebileceğini tanımlar.
YETKI_MATRISI = {
    "TRADER": {
               "arayuz_konulari": ["bakiye çıkışı","bakiye yükleme","satış emri onayı","hisse senedi satışı","yetersiz bakiye uyarısı","emir tipi","hisse senedi alımı","hesap açma formu","bakiye türleri","hesap açma","emir hataları","emir geçmişi","emir iptal etme","bekleyen emirler","emir gönderme","şifremi unuttum","giriş hatası","ilk giriş","kurumsal müşteri formu","müşteri inaktif etme","bireysel müşteri formu","yeni müşteri ekleme","hisse alımı","ayarlar",] # PY sadece müşteri ve işlemle ilgili yardımlara erişebilir
    },
    "VIEWER": {
               "arayuz_konulari": ["bakiye çıkışı","bakiye yükleme","satış emri onayı","hisse senedi satışı","yetersiz bakiye uyarısı","emir tipi","hisse senedi alımı","hesap açma formu","bakiye türleri","hesap açma","emir hataları","emir geçmişi","emir iptal etme","bekleyen emirler","emir gönderme","şifremi unuttum","giriş hatası","ilk giriş","hisse alımı","ayarlar",] # Görüntüleyici hiçbir operasyonel yardım alamaz
    }
} 


def verify_user(username, password):
    # DİKKAT: Bu fonksiyon prototip amaçlıdır. Gerçekte şifreler hash'lenerek karşılaştırılmalıdır.
    sql = "SELECT id, role,first_name, username FROM users WHERE username = ? AND password = ?"
    conn = get_db_connection()
    if not conn: return None
    try:
        cursor = conn.cursor()
        cursor.execute(sql, username, password)
        row = cursor.fetchone()
        if row:
            return {'user_id': row.id, 'role': row.role, 'first_name': row.first_name ,'username': row.username}
        return None
    except Exception as e:
        print(f"Kullanıcı doğrulama hatası: {e}")
        return None
    finally:
        if conn: conn.close()

# Gemini modelini yapılandır
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- Veri Dosyalarını Yükleme ---
try:
    with open('egitim_veritabani.json', 'r', encoding='utf-8') as f:
        knowledge_base = json.load(f)
except FileNotFoundError:
    print("UYARI: 'egitim_veritabani.json' dosyası bulunamadı.")
    knowledge_base = {}

try:
    with open('arayuz_bilgileri.json', 'r', encoding='utf-8') as f:
        ui_info_db = json.load(f)
except FileNotFoundError:
    print("UYARI: 'arayuz_bilgileri.json' dosyası bulunamadı.")
    ui_info_db = {}
try:
    with open('urun_bilgileri.json', 'r', encoding='utf-8') as f:
        product_info_db = json.load(f)
except FileNotFoundError:
    print("UYARI: 'urun_bilgileri.json' dosyası bulunamadı.")
    product_info_db = {}
try:
    with open('hatalar_veritabani.json', 'r', encoding='utf-8') as f:
        error_info_db = json.load(f)
except FileNotFoundError:
    print("UYARI: 'hatalar_veritabani.json' dosyası bulunamadı.")
    error_info_db = {}
try:
    with open('spk_ozetleri.json', 'r', encoding='utf-8') as f:
        spk_info_db = json.load(f)
except FileNotFoundError:
    print("UYARI: 'spk_ozetleri.json' dosyası bulunamadı. SPK bülten özelliği çalışmayacak.")
    spk_info_db = {}

# --- Web Arayüzü için Sohbet Köprüsü --- MESAJLARI DIALOGFLOWA GÖNDERME---
@app.route('/')
def index():
    """Kullanıcıyı giriş ekranına yönlendirir."""
    return send_from_directory('.', 'login.html')

@app.route('/login', methods=['POST'])
def login():
    """Giriş bilgilerini doğrular."""
    data = flask.request.get_json()
    user_data = verify_user(data.get('username'), data.get('password'))
    if user_data:
        return flask.jsonify({"message": "Giriş başarılı", "user": user_data}), 200
    else:
        return flask.jsonify({"error": "Kullanıcı adı veya şifre hatalı."}), 401

@app.route('/chatpage')
def chatpage():
    """Sohbet sayfasını (index.html) sunar."""
    return send_from_directory('.', 'index.html')

# --- Web Arayüzü için Sohbet Köprüsü ---
@app.route('/chat', methods=['POST'])

def chat():
    """Web arayüzünden gelen mesajları alır ve Dialogflow'a gönderir."""
    data = flask.request.get_json()
    user_message = data.get('message')
    session_id = data.get('session_id', str(uuid.uuid4()))
    user_id = data.get('user_id')
    user_role = data.get('user_role') 

    if not user_message:
        return flask.jsonify({"error": "Mesaj boş olamaz"}), 400

    try:
        # Kimlik bilgilerini burada ayarla
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "dialogflow-credentials.json"
        session_client = dialogflow.SessionsClient()
        session = session_client.session_path(DIALOGFLOW_PROJECT_ID, session_id)
        
        # YENİ: Rol bilgisini Dialogflow'a context olarak gönderiyoruz
        context_path = f"{session}/contexts/session-vars"
        context = dialogflow.Context(
            name=context_path,
            lifespan_count=5,
            parameters={"user_role": user_role, "user_id": user_id}
        )
        query_params = dialogflow.QueryParameters(contexts=[context])

        text_input = dialogflow.TextInput(text=user_message, language_code=DIALOGFLOW_LANGUAGE_CODE)
        query_input = dialogflow.QueryInput(text=text_input)
        
        response = session_client.detect_intent(request={"session": session, "query_input": query_input, "query_params": query_params})
        
        bot_response = response.query_result.fulfillment_text
        
        return flask.jsonify({"response": bot_response, "session_id": session_id})

    except Exception as e:
        print(f"Dialogflow API Hatası: {e}")
        return flask.jsonify({"error": "Chatbot ile iletişim kurulamadı."}), 500
    except Exception as e:
        print(f"Dialogflow API Hatası: {e}")
        return flask.jsonify({"error": "Chatbot ile iletişim kurulamadı."}), 500

# --- Ana Webhook Fonksiyonu (YENİDEN DÜZENLENDİ) --- Intent'lere Göre Sorguları İşleme
@app.route('/webhook', methods=['POST'])
def webhook():
    req = flask.request.get_json(force=True)
    try:
        intent_adi = req.get('queryResult', {}).get('intent', {}).get('displayName')
        parameters = req.get('queryResult', {}).get('parameters', {})
        session_path = req.get('session', 'default-session')
        yanit_metni = "Bu konuda size nasıl yardımcı olabileceğimi anlayamadım."
        
        # YENİ: Context'ten kullanıcı rolünü ve ID'sini al
        user_role = "VIEWER" # Varsayılan olarak en düşük yetki
        user_id = None
        output_contexts = req.get('queryResult', {}).get('outputContexts', [])
        for context in output_contexts:
            if "session-vars" in context.get('name', ''):
                context_params = context.get('parameters', {})
                user_role = context_params.get('user_role', 'VIEWER')
                user_id = context_params.get('user_id')
                break

        # YENİ: Yetki Kontrolü
        if not has_permission(user_role, intent_adi, parameters):
            print(f"--- YETKİ REDDEDİLDİ: Rol='{user_role}', İstenen Intent='{intent_adi}' ---")
            return flask.jsonify({'fulfillmentText': "Üzgünüm, bu işlemi gerçekleştirmek için yetkiniz bulunmamaktadır."})
        
        print(f"--- Yetki Onaylandı: Rol='{user_role}', Intent='{intent_adi}' ---")
    
        output_contexts = []
        # YENİ: Hibrit model için Default Fallback Intent kontrolü
        if intent_adi == 'Default Fallback Intent':
            user_query = req.get('queryResult', {}).get('queryText')
            yanit_metni = handle_smart_fallback(user_query)
        # Grup 1: Genel Sorgular
        elif intent_adi == 'Egitim_Terim_Aciklama':
            term = get_string_param(parameters, 'finansal_terim')
            if not term: yanit_metni = "Hangi finansal terim hakkında bilgi almak istediğinizi anlayamadım."
            else: 
                # Önce kendi veritabanımızda arayalım
                yanit_metni = knowledge_base.get(term.lower())
                
                # Eğer kendi veritabanımızda bulamazsak, Gemini'ye soralım
                if not yanit_metni:
                    print(f"--- '{term}' yerel veritabanında bulunamadı. Gemini'ye soruluyor. ---")
                    llm_query = f"'{term}'  terimini bir banka çalışanı için açıkla."
                    yanit_metni = handle_fallback_with_llm(llm_query)
        
        elif intent_adi == 'Sorgu_SPK_Bulteni':
            yanit_metni = handle_spk_bulletin_query(spk_info_db)
        
        elif intent_adi == 'Sorgu_Musteri_Filtrele_Hisse':
            hisse_kodu = get_string_param(parameters, 'Hisse_Kodu')
            if not hisse_kodu:
                yanit_metni = "Hangi hisse senedine sahip olan müşterileri aradığınızı belirtmediniz."
            else:
                yanit_metni = handle_find_clients_by_stock(hisse_kodu)

        elif intent_adi == 'Sorgu_Hesap_Hareketleri':
            musteri_id_param = parameters.get('MusteriID')
            if not musteri_id_param:
                yanit_metni = "Hangi müşterinin hesap hareketlerini sorgulamak istediğinizi belirtmediniz."
            else:
                musteri_id = int(musteri_id_param[0] if isinstance(musteri_id_param, list) else musteri_id_param)
                islem_tipi = get_string_param(parameters, 'IslemTipi')
                adet = parameters.get('adet')
                
                yanit_metni = handle_transaction_history_query(musteri_id, transaction_type=islem_tipi, limit=adet)

        elif intent_adi == 'Sorgu_Kullanici_Aktivitesi':
            kullanici_id_param = parameters.get('KullaniciID')
            adet_param = parameters.get('adet')

            if not kullanici_id_param:
                yanit_metni = "Hangi kullanıcının aktivitelerini sorgulamak istediğinizi belirtmediniz."
            else:
                # DÜZELTME: Gelen parametrenin liste olma durumu kontrol ediliyor.
                kullanici_id = int(kullanici_id_param[0] if isinstance(kullanici_id_param, list) else kullanici_id_param)
                
                # DÜZELTME: 'adet' parametresi de güvenli bir şekilde alınıyor.
                adet = None
                if isinstance(adet_param, list) and adet_param:
                    adet = int(adet_param[0])
                elif isinstance(adet_param, (int, float)):
                    adet = int(adet_param)

                yanit_metni = handle_user_activity_query(kullanici_id, limit=adet)

        elif intent_adi == 'Sorgu_Rapor_Bilgisi':
            # ÖNEMLİ: Dialogflow intent'inde parametre adını 'MusteriID' yerine 'KullaniciID' olarak değiştirmelisin.
            kullanici_id_param = parameters.get('KullaniciID')
            if not kullanici_id_param:
                yanit_metni = "Hangi kullanıcının raporunu sorgulamak istediğinizi belirtmediniz."
            else:
                kullanici_id = int(kullanici_id_param[0] if isinstance(kullanici_id_param, list) else kullanici_id_param)
                yanit_metni = handle_report_query(kullanici_id)

        elif intent_adi == 'Sorgu_Hisse_Detay':
            hisse_kodu = parameters.get('Hisse_Kodu', '').strip()
            # Detay türü artık sadece özet veya fiyat olabilir.
            detay_turu = parameters.get('HisseDetayTuru', 'özet').strip()
            if not hisse_kodu: 
                yanit_metni = "Hangi hisse senedi hakkında detay istediğinizi belirtmediniz."
            else:
                yanit_metni = handle_stock_detail_query(hisse_kodu, detay_turu)

        elif intent_adi == 'Sorgu_Istatistiksel_Analiz':
            user_query = req.get('queryResult', {}).get('queryText')
            yanit_metni = handle_statistical_query(user_query)
        # Grup 2: Sadece Müşteri ID'si Gerektiren Sorgular
        elif intent_adi in ['Portfoy_Listele', 'Portfoy_ToplamDeger_Sorgula']:
            musteri_id_param = parameters.get('MusteriID') # ID @sys.number olduğu için list gelmez
            if not musteri_id_param:
                yanit_metni = "Lütfen işlem yapmak istediğiniz müşterinin ID'sini belirtin."
            else:
                musteri_id = int(musteri_id_param[0] if isinstance(musteri_id_param, list) else musteri_id_param)
                if intent_adi == 'Portfoy_Listele':
                    yanit_metni = handle_list_portfolio(musteri_id)
                elif intent_adi == 'Portfoy_ToplamDeger_Sorgula':
                    yanit_metni = handle_total_value_query(musteri_id)

        # Grup 3: Hem Müşteri ID'si hem de Hisse Kodu Gerektiren Sorgular
        elif intent_adi in ['Portfoy_Adet_Sorgula', 'Portfoy_Maliyet_Sorgula', 'Portfoy_KarZarar_Sorgula']:
            musteri_id_param = parameters.get('MusteriID')
            hisse_kodu = get_string_param(parameters, 'Hisse_Kodu')
            
            if not musteri_id_param:
                yanit_metni = "Lütfen işlem yapmak istediğiniz müşterinin ID'sini belirtin."
            elif not hisse_kodu:
                musteri_id = int(musteri_id_param)
                yanit_metni = f"ID'si {musteri_id} olan müşteri için hangi hisseyi sorgulamak istediğinizi belirtmediniz."
            else:
                musteri_id = int(musteri_id_param[0] if isinstance(musteri_id_param, list) else musteri_id_param)
                if intent_adi == 'Portfoy_Adet_Sorgula':
                    yanit_metni = handle_quantity_query(musteri_id, hisse_kodu)
                elif intent_adi == 'Portfoy_Maliyet_Sorgula':
                    yanit_metni = handle_cost_query(musteri_id, hisse_kodu)
                elif intent_adi == 'Portfoy_KarZarar_Sorgula':
                    yanit_metni = handle_profit_loss_query(musteri_id, hisse_kodu)
        
        elif intent_adi == 'Arayuz_Yardim_Sorgula':
            konu = get_string_param(parameters, 'Arayuz_Konusu')
            if not konu: 
                yanit_metni = "Arayüzün hangi bölümü hakkında yardım almak istediğinizi belirtmediniz."
            else: 
                yanit_metni, output_contexts = handle_ui_help_query(ui_info_db, konu, session_path)
                # Eğer yerel veritabanında bulunamazsa, Gemini'ye sor
                if "bulunamadı" in yanit_metni:
                    print(f"--- '{konu}' için arayüz yardımı bulunamadı. Gemini'ye soruluyor. ---")
                    llm_query = f"Investra adlı bir finansal platformda '{konu}' işlemi nasıl yapılır, adım adım anlat."
                    yanit_metni = handle_fallback_with_llm(llm_query)
                    output_contexts = [] # LLM cevabında context olmaz

        elif intent_adi == 'Sorgu_Emir_Gecmisi':
            musteri_id_param = parameters.get('MusteriID')
            if not musteri_id_param:
                yanit_metni = "Hangi müşterinin emir geçmişini sorgulamak istediğinizi belirtmediniz."
            else:
                musteri_id = int(musteri_id_param[0] if isinstance(musteri_id_param, list) else musteri_id_param)
                emir_durumu = get_string_param(parameters, 'EmirDurumu')
                adet = parameters.get('adet') # Bu bir sayı olduğu için .strip() gerekmez
                
                yanit_metni = handle_order_history_query(musteri_id, status=emir_durumu, limit=adet)
        
        elif intent_adi == 'Sorgu_Urun_Bilgisi':
            konu_param = parameters.get('Urun_Konusu')
            konu = ""
            if isinstance(konu_param, list) and konu_param: konu = konu_param[0].strip()
            elif isinstance(konu_param, str): konu = konu_param.strip()
            if not konu: 
                yanit_metni = "Ürün hakkında hangi konuda bilgi almak istediğinizi belirtmediniz."
            else: 
                yanit_metni, output_contexts = handle_product_info_query(product_info_db, konu, session_path)
        elif intent_adi == 'Sorgu_Urun_Bilgisi - yes':
            yanit_metni = "Yardımcı olabildiğime sevindim! Başka bir sorunuz var mı?"
        elif intent_adi == 'Sorgu_Urun_Bilgisi - no':
            yanit_metni = "Anlıyorum. Daha fazla bilgi veya destek almak için lütfen investra-destek@infina.com.tr adresini ziyaret edin."
        elif intent_adi == 'Sorgu_Hata_Bilgisi':
            konu_param = parameters.get('Hata_Kodu')
            konu = ""
            if isinstance(konu_param, list) and konu_param: konu = konu_param[0].strip()
            elif isinstance(konu_param, str): konu = konu_param.strip()
            
            if not konu: 
                yanit_metni = "Hangi hata hakkında bilgi almak istediğinizi belirtmediniz."
            else:
                # Önce kendi veritabanımızda interaktif bir çözüm var mı diye bakalım
                yanit_metni, output_contexts = handle_error_info_start(error_info_db, konu, session_path)
                
                # Eğer interaktif bir çözüm adımı bulunamazsa, Gemini'ye soralım
                if "bulamadım" in yanit_metni:
                     print(f"--- '{konu}' için interaktif çözüm bulunamadı. Gemini'ye soruluyor. ---")
                     llm_query = f"Bir hisse alım satım uygulamasında bankacı olarak kullandığım platformda '{konu}' hatası alıyorum. Olası nedenleri ve çözüm adımları nelerdir?"
                     yanit_metni = handle_fallback_with_llm(llm_query)
                     output_contexts = [] # LLM cevabında context olmaz
        elif intent_adi in ['Sorgu_Hata_Bilgisi - yes', 'Sorgu_Hata_Bilgisi - no']:
            yanit_metni, output_contexts = handle_error_info_followup(req, error_info_db)
        else:
            yanit_metni = "Bu konuda size nasıl yardımcı olabileceğimi anlayamadım."
            
        response = {'fulfillmentText': yanit_metni, 'outputContexts': output_contexts}
        return flask.jsonify(response)
# --- Diğer Yardımcı Fonksiyonlar ---
    except Exception as e:
        print(f"!!! WEBHOOK İÇİNDE HATA YAKALANDI: {e} !!!")
        # Hata durumunda kullanıcıya nazik bir mesaj gönder
        error_response = {'fulfillmentText': 'Üzgünüm, isteğinizi işlerken beklenmedik bir sorun oluştu.'}
        return flask.jsonify(error_response)

def find_customer_by_id(customer_id):
    """Müşteri bilgilerini veritabanındaki clients tablosundan ID'ye göre çeker."""
    sql = "SELECT id, full_name, phone, status FROM clients WHERE id = ?"
    conn = get_db_connection()
    if not conn: return None
    
    try:
        cursor = conn.cursor()
        cursor.execute(sql, customer_id)
        row = cursor.fetchone()
        if row:
            customer_data = {
                'id': row.id,
                'full_name': row.full_name,
                'phone': row.phone,
                'status': row.status
            }
            return customer_data
        else:
            return None
    except Exception as e:
        print(f"Müşteri bulma sorgu hatası: {e}")
        return None
    finally:
        if conn:
            conn.close()

def find_user_by_id(user_id):
    """Kullanıcı bilgilerini veritabanındaki users tablosundan ID'ye göre çeker."""
    sql = "SELECT id, username, role, first_name, last_name FROM users WHERE id = ?"
    conn = get_db_connection()
    if not conn: return None
    try:
        cursor = conn.cursor()
        cursor.execute(sql, user_id)
        row = cursor.fetchone()
        if row:
            user_data = {
                'user_id': row.id,
                'username': row.username,
                'full_name': f"{row.first_name or ''} {row.last_name or ''}".strip(),
                'role': row.role
            }
            return user_data
        else:
            return None
    except Exception as e:
        print(f"Kullanıcı bulma sorgu hatası: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_string_param(parameters, param_name):
    """Dialogflow'dan gelen bir parametrenin string veya list olmasını yönetir."""
    param_value = parameters.get(param_name)
    if isinstance(param_value, list) and param_value:
        return param_value[0].strip()
    elif isinstance(param_value, str):
        return param_value.strip()
    return "" # Eğer parametre yoksa veya boşsa

def handle_fallback_with_llm(user_query):
    """Dialogflow'un anlamadığı sorguları Gemini LLM'e gönderir."""
    print(f"--- Fallback tetiklendi. Sorgu Gemini'ye gönderiliyor: '{user_query}' ---")
    
    # Gemini API anahtarının ayarlanıp ayarlanmadığını kontrol et
    if not GEMINI_API_KEY or GEMINI_API_KEY == "BURAYA_GEMINI_API_ANAHTARINI_YAPIŞTIR":
        return "Üzgünüm, şu an genel soruları cevaplayamıyorum. Lütfen daha sonra tekrar deneyin."

    try:
        # Gemini modelini seç ve içeriği gönder
        model = genai.GenerativeModel('gemini-1.5-flash')
        # Güvenlik ayarlarını daha esnek hale getirerek bloklanma riskini azalt
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        response = model.generate_content(user_query, safety_settings=safety_settings)
        
        # Modelin cevabını al
        return response.text
    except Exception as e:
        print(f"!!! GEMINI API HATASI: {e} !!!")
        return "Üzgünüm, genel soruları yanıtlarken bir sorunla karşılaştım."

def handle_spk_bulletin_query(database):
    """SPK bülten veritabanından en son özeti alır ve formatlar."""
    last_bulletin = database.get("son_bulten")
    
    if not last_bulletin:
        return "Üzgünüm, şu an için SPK bülten özetine ulaşılamıyor."
        
    tarih = last_bulletin.get("tarih", "Bilinmiyor")
    bulten_no = last_bulletin.get("bulten_no", "Bilinmiyor")
    ozet = last_bulletin.get("ozet", "Özet bulunamadı.")
    
    response = f"**SPK Haftalık Bülteni ({tarih} - No: {bulten_no})**\n\n{ozet}"
    return response

def handle_list_portfolio(customer_id):
    customer_data = find_customer_by_id(customer_id)
    if not customer_data: 
        return f"ID'si '{customer_id}' olan bir müşteri bulunamadı."
    customer_name = customer_data.get('full_name', '')
    
    # GÜNCELLENDİ: Sorgu artık avg_price sütununu da içeriyor.
    sql = """
        SELECT s.code, pi.quantity, pi.avg_price 
        FROM portfolio_items AS pi
        JOIN stocks AS s ON pi.stock_id = s.id
        JOIN portfolios AS p ON pi.portfolio_id = p.id
        WHERE p.client_id = ?
    """
    conn = get_db_connection()
    if not conn: return "Veritabanına bağlanırken bir sorun oluştu."
    try:
        cursor = conn.cursor()
        cursor.execute(sql, customer_id)
        rows = cursor.fetchall()
        if not rows:
            return f"{customer_name} ({customer_id}) adlı müşterinin portföyünde henüz bir varlık bulunmuyor."
        
        yanit = f"{customer_name} ({customer_id}) adlı müşterinin portföyü:\n"
        # GÜNCELLENDİ: Yanıt metni artık ortalama maliyeti de içeriyor.
        for row in rows:
            yanit += f"- {row.quantity} adet {row.code} (Ort. Maliyet: {row.avg_price:.2f} TL)\n"
        return yanit.strip()
    except Exception as e:
        print(f"Portföy listeleme sorgu hatası: {e}")
        return "Müşteri portföyü alınırken bir hata oluştu."
    finally:
        if conn:
            conn.close()

def handle_quantity_query(customer_id, hisse_kodu_sorgu):
    customer_data = find_customer_by_id(customer_id)
    if not customer_data: return f"ID'si '{customer_id}' olan bir müşteri bulunamadı."
    customer_name = customer_data.get('full_name', '')
    
    # DÜZELTME: Sorgu artık PORTFOLIO tablosunu da içeriyor.
    sql = """
        SELECT pi.quantity 
        FROM portfolio_items AS pi 
        JOIN stocks AS s ON pi.stock_id = s.id
        JOIN portfolios AS p ON pi.portfolio_id = p.id
        WHERE p.client_id = ? AND s.code = ?
    """
    conn = get_db_connection()
    if not conn: return "Veritabanına bağlanırken bir sorun oluştu."
    try:
        cursor = conn.cursor()
        cursor.execute(sql, customer_id, hisse_kodu_sorgu.upper())
        row = cursor.fetchone()
        if row:
            return f"{customer_name} ({customer_id}) adlı müşterinin portföyünde {row.quantity} adet {hisse_kodu_sorgu.upper()} hissesi bulunmaktadır."
        else:
            return f"{customer_name} ({customer_id}) adlı müşterinin portföyünde '{hisse_kodu_sorgu.upper()}' adlı bir hisse senedi bulunmuyor."
    except Exception as e:
        print(f"Hisse adedi sorgulama hatası: {e}")
        return "Müşterinin hisse adedi bilgisi alınırken bir hata oluştu."
    finally:
        if conn:
            conn.close()

def handle_cost_query(customer_id, hisse_kodu_sorgu):
    customer_data = find_customer_by_id(customer_id)
    if not customer_data: return f"ID'si '{customer_id}' olan bir müşteri bulunamadı."
    customer_name = customer_data.get('full_name', '')
    
    # DÜZELTME: Sorgu artık PORTFOLIO tablosunu da içeriyor.
    sql = """
        SELECT pi.quantity, pi.avg_price 
        FROM portfolio_items AS pi 
        JOIN stocks AS s ON pi.stock_id = s.id
        JOIN portfolios AS p ON pi.portfolio_id = p.id
        WHERE p.client_id = ? AND s.code = ?
    """
    conn = get_db_connection()
    if not conn: return "Veritabanına bağlanırken bir sorun oluştu."
    try:
        cursor = conn.cursor()
        cursor.execute(sql, customer_id, hisse_kodu_sorgu.upper())
        row = cursor.fetchone()
        if row:
            return f"{customer_name} ({customer_id}) adlı müşterinin portföyündeki {row.quantity} adet {hisse_kodu_sorgu.upper()} hissesi, birim başına ortalama {row.avg_price:.2f} TL maliyetle alınmıştır."
        else:
            return f"{customer_name} ({customer_id}) adlı müşterinin portföyünde '{hisse_kodu_sorgu.upper()}' adlı bir hisse senedi bulunmuyor."
    except Exception as e:
        print(f"Hisse maliyeti sorgulama hatası: {e}")
        return "Müşterinin hisse maliyet bilgisi alınırken bir hata oluştu."
    finally:
        if conn:
            conn.close()

def handle_profit_loss_query(customer_id, hisse_kodu_sorgu):
    customer_data = find_customer_by_id(customer_id)
    if not customer_data: return f"ID'si '{customer_id}' olan bir müşteri bulunamadı."
    customer_name = customer_data.get('full_name', '')
    
    # DÜZELTME: Sorgu artık PORTFOLIO tablosunu da içeriyor.
    sql = """
        SELECT pi.quantity, pi.avg_price 
        FROM portfolio_items AS pi 
        JOIN stocks AS s ON pi.stock_id = s.id
        JOIN portfolios AS p ON pi.portfolio_id = p.id
        WHERE p.client_id = ? AND s.code = ?
    """
    conn = get_db_connection()
    if not conn: return "Veritabanına bağlanırken bir sorun oluştu."
    try:
        cursor = conn.cursor()
        cursor.execute(sql, customer_id, hisse_kodu_sorgu.upper())
        row = cursor.fetchone()
        if not row:
            return f"{customer_name} ({customer_id}) adlı müşterinin portföyünde '{hisse_kodu_sorgu.upper()}' adlı bir hisse senedi bulunmuyor."
        
        adet = row.quantity
        maliyet = float(row.avg_price)
        guncel_fiyat = get_stock_price(hisse_kodu_sorgu)
        if guncel_fiyat is None: 
            return f"'{hisse_kodu_sorgu.upper()}' için güncel fiyat bilgisi alınamadığı için kâr/zarar hesaplanamıyor."
        
        toplam_maliyet = adet * maliyet
        guncel_deger = adet * guncel_fiyat
        kar_zarar = guncel_deger - toplam_maliyet
        
        yanit = f"{customer_name} ({customer_id}) müşterisinin {hisse_kodu_sorgu.upper()} pozisyonu:\n"
        yanit += f"- Toplam Maliyet: {toplam_maliyet:,.2f} TL\n"
        yanit += f"- Güncel Değer: {guncel_deger:,.2f} TL\n"
        if kar_zarar >= 0:
            yanit += f"- Anlık Kâr: {kar_zarar:,.2f} TL"
        else:
            yanit += f"- Anlık Zarar: {kar_zarar:,.2f} TL"
        return yanit
    except Exception as e:
        print(f"Kâr/zarar sorgulama hatası: {e}")
        return "Müşterinin kâr/zarar durumu hesaplanırken bir hata oluştu."
    finally:
        if conn:
            conn.close()

def handle_total_value_query(customer_id):
    customer_data = find_customer_by_id(customer_id)
    if not customer_data: return f"ID'si '{customer_id}' olan bir müşteri bulunamadı."
    customer_name = customer_data.get('full_name', '')
    
    # DÜZELTME: Sorgu artık PORTFOLIO tablosunu da içeriyor.
    sql = """
        SELECT s.code, pi.quantity 
        FROM portfolio_items AS pi 
        JOIN stocks AS s ON pi.stock_id = s.id
        JOIN portfolios AS p ON pi.portfolio_id = p.id
        WHERE p.client_id = ?
    """
    conn = get_db_connection()
    if not conn: return "Veritabanına bağlanırken bir sorun oluştu."
    try:
        cursor = conn.cursor()
        cursor.execute(sql, customer_id)
        rows = cursor.fetchall()
        if not rows:
            return f"{customer_name} ({customer_id}) adlı müşterinin portföyünde henüz bir varlık bulunmuyor."
        
        toplam_portfoy_degeri = 0
        ulasilamayan_hisseler = []
        for row in rows:
            adet = row.quantity
            hisse_kodu = row.code
            guncel_fiyat = get_stock_price(hisse_kodu)
            if guncel_fiyat is not None:
                toplam_portfoy_degeri += adet * guncel_fiyat
            else:
                ulasilamayan_hisseler.append(hisse_kodu)
        
        yanit = f"{customer_name} ({customer_id}) adlı müşterinin portföyünün anlık toplam değeri: {toplam_portfoy_degeri:,.2f} TL."
        if ulasilamayan_hisseler:
            yanit += f"\n(Not: {', '.join(ulasilamayan_hisseler)} için güncel fiyata ulaşılamadığı için hesaba katılmadı.)"
        return yanit
    except Exception as e:
        print(f"Toplam portföy değeri hesaplama hatası: {e}")
        return "Müşterinin toplam portföy değeri hesaplanırken bir hata oluştu."
    finally:
        if conn:
            conn.close()

def handle_find_clients_by_stock(hisse_kodu_sorgu):
    """Verilen hisse senedine sahip olan tüm müşterileri veritabanından bulur."""
    print(f"--- '{hisse_kodu_sorgu}' hissesine sahip müşteriler aranıyor... ---")
    
    # Bu karmaşık sorgu, 4 tabloyu birleştirerek doğru sonuca ulaşır.
    sql = """
        SELECT DISTINCT c.id, c.full_name
        FROM clients AS c
        JOIN portfolios AS p ON c.id = p.client_id
        JOIN portfolio_items AS pi ON p.id = pi.portfolio_id
        JOIN stocks AS s ON pi.stock_id = s.id
        WHERE s.code = ?
    """
    
    conn = get_db_connection()
    if not conn: return "Veritabanına bağlanırken bir sorun oluştu."

    try:
        cursor = conn.cursor()
        cursor.execute(sql, hisse_kodu_sorgu.upper())
        rows = cursor.fetchall()
        
        if not rows:
            return f"Portföyünde '{hisse_kodu_sorgu.upper()}' hissesi bulunan bir müşteri bulunamadı."
        
        musteri_sayisi = len(rows)
        yanit = f"Portföyünde '{hisse_kodu_sorgu.upper()}' hissesi bulunan {musteri_sayisi} müşteri bulundu:\n"
        for row in rows:
            yanit += f"- {row.full_name} (ID: {row.id})\n"
        return yanit.strip()
    except Exception as e:
        print(f"Hisseye göre müşteri arama hatası: {e}")
        return "Müşteriler aranırken bir veritabanı hatası oluştu."
    finally:
        if conn:
            conn.close()

def handle_order_history_query(customer_id, status=None, limit=None):
    """Verilen müşteri ID'si için emir geçmişini, opsiyonel filtrelere göre döndürür."""
    customer_data = find_customer_by_id(customer_id)
    if not customer_data: 
        return f"ID'si '{customer_id}' olan bir müşteri bulunamadı."
    
    customer_name = customer_data.get('full_name', '')
    
    # SQL sorgusunu dinamik olarak oluşturalım
    params = [customer_id]
    sql = """
        SELECT TOP (?) T.submitted_at, S.code, T.order_type, T.quantity, T.status
        FROM trade_orders AS T
        JOIN stocks AS S ON T.stock_id = S.id
        WHERE T.client_id = ?
    """
    
    # Eğer bir durum filtresi varsa, sorguya ekle
    if status:
        sql += " AND T.status = ?"
        params.append(status.upper()) # Veritabanındaki değere (PENDING, EXECUTED vb.) uyması için
    
    sql += " ORDER BY T.submitted_at DESC"
    
    # Limit parametresini en başa ekle (TOP (?) için)
    # Eğer limit belirtilmemişse, varsayılan olarak son 5 emri getir.
    params.insert(0, int(limit) if limit else 5)

    conn = get_db_connection()
    if not conn: return "Veritabanına bağlanırken bir sorun oluştu."

    try:
        cursor = conn.cursor()
        cursor.execute(sql, *params)
        rows = cursor.fetchall()
        
        if not rows:
            filter_text = f" '{status}' durumunda" if status else ""
            return f"{customer_name} ({customer_id}) adlı müşterinin{filter_text} bir emri bulunamadı."
        
        yanit = f"{customer_name} ({customer_id}) adlı müşterinin son emirleri:\n"
        for row in rows:
            # Tarihi daha okunabilir bir formata çevirelim
            formatted_date = row.submitted_at.strftime('%d-%m-%Y %H:%M')
            yanit += f"- {formatted_date}: {row.quantity} adet {row.code} {row.order_type} ({row.status})\n"
        return yanit.strip()
    except Exception as e:
        print(f"Emir geçmişi sorgulama hatası: {e}")
        return "Müşterinin emir geçmişi alınırken bir veritabanı hatası oluştu."
    finally:
        if conn:
            conn.close()

def handle_user_activity_query(user_id, limit=None):
    """Verilen kullanıcı ID'sine göre log kayıtlarını döndürür."""
    print(f"--- ID'si '{user_id}' olan kullanıcı için log kayıtları aranıyor... ---")
    
    # SQL sorgusunu dinamik olarak oluşturalım
    # TOP (?) MS SQL Server'a özgüdür.
    # Eğer limit belirtilmemişse, varsayılan olarak son 5 log kaydını getir.
    limit_clause = f"TOP {int(limit)}" if limit and int(limit) > 0 else "TOP 5" 
    
    sql = f"""
        SELECT {limit_clause} l.timestamp, u.username, l.action, l.details
        FROM logs AS l
        JOIN users AS u ON l.user_id = u.id
        WHERE l.user_id = ?
        ORDER BY l.timestamp DESC
    """
    
    conn = get_db_connection()
    if not conn: return "Veritabanına bağlanırken bir sorun oluştu."

    try:
        cursor = conn.cursor()
        cursor.execute(sql, user_id)
        rows = cursor.fetchall()
        
        if not rows:
            return f"ID'si '{user_id}' olan kullanıcı için bir aktivite kaydı bulunamadı."
        
        username = rows[0].username
        yanit = f"'{username}' (ID: {user_id}) kullanıcısının son aktiviteleri:\n"
        for row in rows:
            formatted_date = row.timestamp.strftime('%d-%m-%Y %H:%M:%S')
            yanit += f"- [{formatted_date}] Eylem: {row.action} | Detay: {row.details}\n"
        return yanit.strip()
    except Exception as e:
        print(f"Kullanıcı aktivitesi sorgulama hatası: {e}")
        return "Kullanıcı aktiviteleri aranırken bir veritabanı hatası oluştu."
    finally:
        if conn:
            conn.close()

def handle_transaction_history_query(customer_id, transaction_type=None, limit=None):
    """Verilen müşteri ID'si için hesap hareketlerini, opsiyonel filtrelere göre döndürür."""
    customer_data = find_customer_by_id(customer_id)
    if not customer_data: 
        return f"ID'si '{customer_id}' olan bir müşteri bulunamadı."
    
    customer_name = customer_data.get('full_name', '')
    
    # SQL sorgusunu dinamik olarak oluşturalım
    params = []
    # TOP (?) MS SQL Server'a özgüdür.
    limit_clause = f"TOP {int(limit)}" if limit and int(limit) > 0 else "TOP 5" # Varsayılan olarak son 5 işlem
    
    sql = f"""
        SELECT {limit_clause} t.transaction_date, t.amount, t.transaction_type, t.description
        FROM transactions AS t
        WHERE t.client_id = ?
    """
    params.append(customer_id)
    
    # Eğer bir işlem tipi filtresi varsa, sorguya ekle
    if transaction_type:
        sql += " AND t.transaction_type = ?"
        params.append(transaction_type.upper()) # Veritabanındaki değere (DEPOSIT, WITHDRAWAL vb.) uyması için
    
    sql += " ORDER BY t.transaction_date DESC"
    
    conn = get_db_connection()
    if not conn: return "Veritabanına bağlanırken bir sorun oluştu."

    try:
        cursor = conn.cursor()
        cursor.execute(sql, *params)
        rows = cursor.fetchall()
        
        if not rows:
            filter_text = f" '{transaction_type}' tipinde" if transaction_type else ""
            return f"{customer_name} ({customer_id}) adlı müşterinin{filter_text} bir hesap hareketi bulunamadı."
        
        yanit = f"{customer_name} ({customer_id}) adlı müşterinin son hesap hareketleri:\n"
        for row in rows:
            # Tarihi daha okunabilir bir formata çevirelim
            formatted_date = row.transaction_date.strftime('%d-%m-%Y') if row.transaction_date else 'Tarih Yok'
            # Miktarı pozitif/negatif olarak gösterelim
            amount_str = f"+{row.amount:,.2f}" if row.transaction_type == 'DEPOSIT' else f"-{row.amount:,.2f}"
            yanit += f"- {formatted_date}: {amount_str} ({row.transaction_type}) - Açıklama: {row.description}\n"
        return yanit.strip()
    except Exception as e:
        print(f"Hesap hareketleri sorgulama hatası: {e}")
        return "Müşterinin hesap hareketleri alınırken bir veritabanı hatası oluştu."
    finally:
        if conn:
            conn.close()

def handle_report_query(user_id):
    """Verilen kullanıcı (çalışan) ID'si için en son raporu döndürür."""
    user_data = find_user_by_id(user_id)
    if not user_data: 
        return f"ID'si '{user_id}' olan bir kullanıcı (çalışan) bulunamadı."
    
    user_name = user_data.get('full_name', '')
    
    # GÜNCELLENDİ: Sorgudan 'report_type' kaldırıldı.
    sql = """
        SELECT TOP 1 r.report_file_url, r.created_at
        FROM reports AS r
        WHERE r.user_id = ?
        ORDER BY r.created_at DESC
    """
    
    conn = get_db_connection()
    if not conn: return "Veritabanına bağlanırken bir sorun oluştu."

    try:
        cursor = conn.cursor()
        cursor.execute(sql, user_id)
        row = cursor.fetchone()
        
        if not row:
            return f"'{user_name}' ({user_id}) kullanıcısı tarafından oluşturulmuş bir rapor bulunamadı."
        
        # GÜNCELLENDİ: Yanıt metninden 'report_type' kaldırıldı.
        formatted_date = row.created_at.strftime('%d-%m-%Y')
        yanit = (f"'{user_name}' ({user_id}) kullanıcısı tarafından {formatted_date} tarihinde oluşturulan son "
                 f"rapora şu linkten ulaşabilirsiniz:\n{row.report_file_url}")
        return yanit
    except Exception as e:
        print(f"Rapor sorgulama hatası: {e}")
        return "Kullanıcının raporları alınırken bir veritabanı hatası oluştu."
    finally:
        if conn:
            conn.close()

def has_permission(user_role, intent_name, parameters):
    """Kullanıcının rolünün, istenen Arayüz Yardım konusuna erişimi olup olmadığını kontrol eder."""
    
    # KURAL 1: Admin rolü her zaman, her şeye yetkilidir.
    if user_role == 'ADMIN':
        return True

    # KURAL 2: Arayüz yardımı dışındaki tüm intent'ler herkese açıktır.
    if intent_name != 'Arayuz_Yardim_Sorgula':
        return True

    # KURAL 3: Arayüz yardımı için özel konu bazlı kontrol
    konu = get_string_param(parameters, 'Arayuz_Konusu')
    # Eğer bir konu belirtilmemişse, genel bir yardım sorusudur, izin ver.
    if not konu:
        return True
        
    allowed_topics = YETKI_MATRISI.get(user_role, {}).get('arayuz_konulari', [])
    for allowed_topic in allowed_topics:
        if allowed_topic in konu:
            return True # Konu, kullanıcının izinli konuları arasındaysa, izin ver.
            
    # Eğer konu, kullanıcının izinli konuları arasında değilse, yetki yok.
    return False

def handle_smart_fallback(user_query):
    """
    Gelen sorgunun veritabanıyla mı yoksa genel kültürle mi ilgili olduğunu anlar
    ve doğru aracı (SQL Ajanı veya Genel LLM) göreve çağırır.
    """
    if not llm or not sql_agent:
        return "Üzgünüm, analitik sorgu motoru şu an aktif değil."

    print(f"--- Akıllı Fallback tetiklendi. Sorgu: '{user_query}' ---")
    
    classification_prompt = f"""
    Aşağıdaki kullanıcı sorgusunun amacını sınıflandır. Cevabın SADECE 'DATABASE' veya 'GENERAL' olsun.
    DATABASE: Eğer sorgu müşteriler, hisse senetleri, portföyler, emirler, bakiye, hesap hareketleri, finansal raporlar gibi Investra veritabanında bulunabilecek bilgilerle ilgiliyse.
    GENERAL: Eğer sorgu genel kültür, sohbet, fıkra, tanım gibi veritabanıyla ilgisi olmayan bir konuysa.
    Kullanıcı Sorgusu: "{user_query}"
    Sınıflandırma:
    """
    
    try:
        classification_response = llm.invoke(classification_prompt)
        classification = classification_response.content.strip().upper()
        print(f"   -> Sorgu Sınıfı: {classification}")

        if "DATABASE" in classification:
            print("   -> Yönlendirme: LangChain SQL Ajanı")
            return handle_statistical_query(user_query)
        else: # GENERAL
            print("   -> Yönlendirme: Genel Gemini Modeli")
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content(user_query)
            return response.text

    except Exception as e:
        print(f"!!! AKILLI FALLBACK HATASI: {e} !!!")
        return "Sorgunuzu işlerken bir sorunla karşılaştım."

def handle_statistical_query(user_query):
    """Kullanıcının istatistiksel sorusunu LangChain SQL Ajanına gönderir."""
    if not sql_agent:
        return "Üzgünüm, analitik sorgu motoru şu an aktif değil."
    
    print(f"--- LangChain SQL Ajanı tetiklendi. ---")
    try:
        # GÜNCELLENDİ: Prompt, ajana sadece ham sonucu döndürmesini söylüyor.
        prompt = f"""
        Aşağıdaki Türkçe soruyu cevaplamak için **Microsoft SQL Server** uyumlu bir sorgu oluştur ve çalıştır.
        MS SQL Server'da satır sınırlamak için `LIMIT` yerine `TOP` ifadesini kullan.
        SADECE SQL sorgusunun ham sonucunu döndür, başka hiçbir açıklama yapma.
        Kullanıcı sorusu: {user_query}
        """

        result = sql_agent.invoke(prompt)

        print(f"--- LangChain Ajanının Ham Çıktısı: {result} ---")

        final_answer = None

# Önce output alanına bak
        if isinstance(result, dict) and result.get("output"):
            final_answer = str(result["output"]).strip()

# Eğer output boşsa intermediate_steps kullan
        if not final_answer and result.get("intermediate_steps"):
            try:
                last_observation = str(result["intermediate_steps"][-1][1])
                final_answer = last_observation.strip()
            except (IndexError, KeyError):
                pass

        if not final_answer:
            final_answer = "İsteğiniz anlaşıldı ancak veritabanından bir sonuç alınamadı."

        print(f"--- Kullanıcıya Gönderilecek Nihai Cevap: {final_answer} ---")
        return final_answer

    except Exception as e:
        print(f"!!! LANGCHAIN AJAN HATASI: {e} !!!")
        return "İstatistiksel analiz yapılırken bir sorunla karşılaştım."
def handle_general_query(user_query):
    """Kullanıcının genel kültür sorusunu standart Gemini'ye gönderir."""
    print(f"--- Standart Gemini tetiklendi. ---")
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(user_query)
        return response.text
    except Exception as e:
        print(f"!!! GEMINI API HATASI: {e} !!!")
        return "Üzgünüm, bu soruya cevap verirken bir sorunla karşılaştım."

def handle_ui_help_query(database, topic, session_path):
    """
    UI yardım veritabanından ilgili konunun açıklamasını bulur.
    Bulamazsa, ana webhook fonksiyonunun Gemini'ye sorması için özel bir mesaj döndürür.
    """
    topic_lower = topic.lower()
    answer = None # Başlangıçta boş
    found_key = None

    exact_match = database.get(topic_lower)
    if exact_match:
        answer = exact_match
        found_key = topic_lower
    else:
        for key, value in database.items():
            if key in topic_lower:
                answer = value
                found_key = key
                break
    
    # Eğer bir cevap bulunduysa, interaktif diyaloğu başlat
    if found_key:
        answer += "\n\nSormak istediğiniz başka bir şey varsa lütfen çekinmeyin"
        context_name = f"{session_path}/contexts/arayuz-yardim-takip"
        output_context = {
            "name": context_name,
            "lifespanCount": 2,
            "parameters": {"Arayuz_Konusu": found_key}
        }
        return answer, [output_context]
    
    # Eğer cevap bulunamazsa, Gemini'ye sormak için özel mesajı döndür
    return "Bu konuda bir yardım metni bulunamadı.", []

def handle_product_info_query(database, topic, session_path):
    """
    Ürün bilgi veritabanından ilgili konunun açıklamasını bulur.
    Bulamazsa, ana webhook fonksiyonunun Gemini'ye sorması için özel bir mesaj döndürür.
    """
    topic_lower = topic.lower()
    answer = None
    found_key = None

    exact_match = database.get(topic_lower)
    if exact_match:
        answer = exact_match
        found_key = topic_lower
    else:
        for key, value in database.items():
            if key in topic_lower:
                answer = value
                found_key = key
                break
    
    if found_key:
        answer += "\n\nBu bilgi yeterli oldu mu?"
        context_name = f"{session_path}/contexts/urun-bilgisi-takip"
        output_context = {
            "name": context_name,
            "lifespanCount": 2,
            "parameters": {"Urun_Konusu": found_key}
        }
        return answer, [output_context]
    
    # Eğer cevap bulunamazsa, Gemini'ye sormak için özel mesajı döndür
    return "Bu ürün özelliği hakkında bir bilgi metni bulunamadı.", []

def handle_error_info_start(database, topic, session_path):# """Verilen konu için hata bilgisi metnini döndürür."""
    topic_lower = topic.lower()
    error_data = None
    for key, value in database.items():
        if key in topic_lower:
            error_data = value
            topic_lower = key
            break
    
    if not error_data or 'adimlar' not in error_data:
        return "Bu hata konusu hakkında bir çözüm adımı bulamadım.", []

    first_step = error_data['adimlar'][0]
    response_text = first_step['soru']
    
    context_name = f"{session_path}/contexts/hata-takip"
    output_context = {
        "name": context_name,
        "lifespanCount": 2,
        "parameters": {"hata_konusu": topic_lower, "adim_index": 0}
    }
    return response_text, [output_context]

def handle_error_info_followup(request, database):# """Hata bilgisi için takip sorusunu işler."""
    intent_name = request['queryResult']['intent']['displayName']
    contexts = request['queryResult'].get('outputContexts', [])
    
    active_context = None
    for context in contexts:
        if "hata-takip" in context.get('name', ''):
            active_context = context
            break
            
    if not active_context:
        return "Üzgünüm, hangi soruna cevap verdiğinizi anlayamadım.", []
        
    params = active_context.get('parameters', {})
    hata_konusu = params.get('hata_konusu')
    adim_index = int(params.get('adim_index', 0))
    
    error_data = database.get(hata_konusu)
    if not error_data or adim_index >= len(error_data.get('adimlar', [])):
        return "Bir hata oluştu, sorun giderme adımları bulunamadı.", []

    current_step = error_data['adimlar'][adim_index]
    
    if intent_name.endswith('- yes'):
        response_text = current_step.get('evet_yanit', "Anlaşıldı.")
    else: # no
        response_text = current_step.get('hayir_yanit', "Anlaşıldı.")
        
    new_adim_index = adim_index + 1
    if new_adim_index < len(error_data['adimlar']):
        next_step = error_data['adimlar'][new_adim_index]
        response_text += " " + next_step.get('soru', "")
        
        context_name = active_context['name']
        output_context = {
            "name": context_name,
            "lifespanCount": 2,
            "parameters": {"hata_konusu": hata_konusu, "adim_index": new_adim_index}
        }
        return response_text, [output_context]
    else:
        return response_text, []

# -------------------------------------------------------------------------------------------------------------------------------------------------------------

# --- Hisse Fiyatı Çekme Fonksiyonları ---
def handle_stock_detail_query(hisse_kodu, detay_turu):
    """Gelen detay türüne göre ilgili fonksiyonu çağırır."""
    detay_turu_lower = detay_turu.lower()
    if 'özet' in detay_turu_lower or 'fiyat' in detay_turu_lower or 'detay' in detay_turu_lower:
        return get_stock_summary(hisse_kodu)
    # Performans ve teknik göstergeler için veritabanında yeterli veri yok.
    else:
        return f"'{detay_turu}' hakkında nasıl bir bilgi istediğinizi anlayamadım. Lütfen 'özet' veya 'fiyat' gibi konular belirtin."

def get_stock_summary(hisse_kodu):
    """Bir hissenin özetini veritabanındaki stocks tablosundan çeker."""
    ticker = hisse_kodu.upper()
    print(f"--- '{ticker}' İÇİN ÖZET BİLGİSİ VERİTABANINDAN ÇEKİLİYOR ---")
    
    # Veritabanı şemanızdaki sütun adlarına göre güncellendi
    sql = "SELECT code, price, price FROM stocks WHERE code = ?"
    conn = get_db_connection()
    if not conn: return "Veritabanına bağlanırken bir sorun oluştu."
    
    try:
        cursor = conn.cursor()
        cursor.execute(sql, ticker)
        row = cursor.fetchone()
        
        if row:
            # DÜZELTME: Değerlerin None olup olmadığını kontrol et
            price_str = f"{row.price:.2f}" if row.price is not None else "N/A"
            price_str = f"{row.price:.2f}" if row.price is not None else "N/A"

            yanit = f"{row.code} için özet:\n"
            yanit += f"- Anlık Fiyat: {price_str}\n"
            yanit += f"- Önceki Kapanış: {price_str}"
            return yanit
        else:
            return f"'{ticker}' için özet bilgisi bulunamadı."
    except Exception as e:
        print(f"Hisse özeti sorgu hatası: {e}")
        return f"'{ticker}' için özet bilgisi alınırken bir hata oluştu."
    finally:
        if conn: conn.close()

# --- Hisse Fiyatı Çekme Fonksiyonu (Artık sadece kâr/zarar gibi eski fonksiyonlar için kullanılıyor) ---
def get_stock_price(hisse_kodu):
    """Sadece anlık fiyat bilgisini veritabanındaki stocks tablosundan çeker."""
    if GELISTIRME_MODU:
        sahte_fiyatlar = {'EREGL': 48.75, 'THYAO': 255.50}
        return sahte_fiyatlar.get(hisse_kodu.upper())
    else:
        ticker = hisse_kodu.upper()
        sql = "SELECT price FROM stocks WHERE code = ?"
        conn = get_db_connection()
        if not conn: return None
        
        try:
            cursor = conn.cursor()
            cursor.execute(sql, ticker)
            row = cursor.fetchone()
            if row:
                return float(row.price)
            return None
        except Exception as e:
            print(f"Hisse fiyatı sorgu hatası: {e}")
            return None
        finally:
            if conn: conn.close()

if __name__ == '__main__': # Flask uygulamasını başlat
    # Başlangıçta bir test bağlantısı yapalım
    print("Program başlarken veritabanı bağlantısı test ediliyor...")
    conn = get_db_connection()
    if conn:
        conn.close()
        print("Başlangıç testi başarılı.")
    else:
        print("Başlangıç testi başarısız. Lütfen veritabanı ayarlarınızı kontrol edin.")
    
    app.run(host='0.0.0.0', port=5000)