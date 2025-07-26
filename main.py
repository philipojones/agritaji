import os
import uuid # Kept, though less used for SMS sessions as phone number is key. Could be removed if not needed elsewhere.
import requests # For external API calls like OpenWeatherMap
from flask import Flask, request, jsonify, render_template, make_response
import google.generativeai as genai
from typing import Dict, List, Optional
from dotenv import load_dotenv
import africastalking
import logging
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("agricultural_advisory_bot.log"), # Consolidated log file
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Configure Gemini API
GEMINI_API_KEY = os.getenv("API_KEY") # Using API_KEY as per your provided structure
if not GEMINI_API_KEY:
    logger.error("API_KEY environment variable not set. Gemini features will be unavailable.")
    gemini_model = None
else:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel("gemini-1.5-flash") # Using gemini-1.5-flash
        logger.info("Gemini API configured successfully.")
    except Exception as e:
        logger.error(f"Failed to configure Gemini API: {str(e)}. Gemini features will be unavailable.")
        gemini_model = None

# Configure Africa's Talking
AT_USERNAME = os.getenv("AT_USERNAME") # Default to sandbox if not set
AT_API_KEY = os.getenv("AT_API_KEY")
AT_SHORTCODE = os.getenv("AT_SHORTCODE") # Important for inbound SMS and some outbound cases

if not AT_API_KEY:
    raise ValueError("AT_API_KEY environment variable not set. Please set it.")
# if not AT_SHORTCODE:
#     logger.warning("AT_SHORTCODE environment variable not set. Outbound SMS might use default sender ID.")

africastalking.initialize(AT_USERNAME, AT_API_KEY)
sms = africastalking.SMS
logger.info("Africa's Talking initialized.")

# OpenWeatherMap API Key
OPENWEATHER_API_KEY = os.getenv('OPENWEATHER_API_KEY')
if not OPENWEATHER_API_KEY:
    logger.warning("OPENWEATHER_API_KEY not set. Weather data will be simulated.")

# --- In-memory storage for sessions ---
# USSD sessions: stores current step and data for multi-step USSD interactions
ussd_sessions: Dict[str, Dict] = {}
# SMS conversations: stores chat history for free-form SMS interactions
sms_conversations: Dict[str, List[Dict]] = {} 

# Session timeout for cleanup (e.g., if a user abandons a USSD session)
SESSION_TIMEOUT = timedelta(minutes=10)

# --- Helper Functions for Agricultural Data & External APIs ---

def get_current_weather(location_name="Dar es Salaam", lat=-6.8235, lon=39.2695):
    """Fetches current weather data from OpenWeatherMap."""
    if not OPENWEATHER_API_KEY:
        logger.warning("OpenWeatherMap API key not set. Returning simulated weather data.")
        # Current time (Friday, July 25, 2025 at 12:19:38 PM EAT) adjusted to now.
        return {
            "temperature": 28,
            "humidity": 75,
            "description": "Scattered clouds",
            "wind_speed": 5,
            "city": location_name,
            "forecast_summary": "Sunny with chances of afternoon showers for the next 3 days."
        }
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
        response = requests.get(url)
        response.raise_for_status() # Raise an exception for HTTP errors
        data = response.json()
        return {
            "temperature": data['main']['temp'],
            "humidity": data['main']['humidity'],
            "description": data['weather'][0]['description'].capitalize(),
            "wind_speed": data['wind']['speed'],
            "city": data['name'],
            "forecast_summary": "Detailed forecast unavailable without a separate forecast API call, but generally fair for the next few days." # Simplified for this example
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching weather from OpenWeatherMap: {e}")
        return None
    except KeyError as e:
        logger.error(f"Unexpected weather data structure: {e} - {data}")
        return None

def get_crop_prices(crop_name: str, region: str = "Dar es Salaam") -> str:
    """Simulates fetching real-time crop prices.
    In a real system, this would query FAO, local government APIs, or TMX.
    """
    # Prices adjusted to be current as of July 2025 (simulated)
    crop_prices = {
        "maize": {"Dar es Salaam": "780 TZS/kg", "Iringa": "720 TZS/kg", "Mbeya": "750 TZS/kg"},
        "beans": {"Dar es Salaam": "1950 TZS/kg", "Morogoro": "1800 TZS/kg"},
        "rice": {"Dar es Salaam": "1300 TZS/kg", "Mwanza": "1200 TZS/kg"},
        "tomato": {"Dar es Salaam": "1650 TZS/kg", "Arusha": "1450 TZS/kg"},
    }
    # Normalize crop name for lookup
    normalized_crop = crop_name.lower().replace(" ", "")
    if normalized_crop in crop_prices and region in crop_prices[normalized_crop]:
        return f"Bei ya sasa ya {crop_name.capitalize()} {region}: {crop_prices[normalized_crop][region]}."
    return f"Bei ya {crop_name.capitalize()} {region} haipatikani kwa sasa. Jaribu zao au eneo tofauti."

def get_crop_price_forecast(crop_name: str) -> str:
    """Simulates time-series forecasting for crop prices.
    In a real system, this would be based on an ML model.
    """
    forecasts = {
        "maize": "Ongezeko kidogo la 3-5% linatarajiwa katika wiki mbili zijazo kutokana na mahitaji ya msimu.",
        "beans": "Bei zinatarajiwa kuwa tulivu kwa mwezi ujao.",
        "rice": "Kupungua kwa asilimia 2-4% kunaweza kutokea mwezi ujao kadiri mavuno mapya yanavyoingia sokoni.",
        "tomato": "Bei zinaweza kubadilika sana katika wiki zijazo kutokana na usambazaji usiotabirika.",
    }
    normalized_crop = crop_name.lower().replace(" ", "")
    if normalized_crop in forecasts:
        return f"Utabiri wa {crop_name.capitalize()}: {forecasts[normalized_crop]}"
    return f"Utabiri wa {crop_name.capitalize()} haupatikani."

def get_logistics_info(item_type: str = "general") -> str:
    """Simulates fetching logistics and storage information."""
    if item_type.lower() == "grain storage":
        return "Vituo vikuu vya kuhifadhia nafaka karibu na Dar es Salaam ni maghala ya NFRA Pugu na Kurasini. Uwezo hubadilika kulingana na msimu. Wasiliana nao moja kwa moja kwa upatikanaji."
    elif item_type.lower() == "transport":
        return "Kwa usafirishaji, fikiria vyama vya malori vya ndani au watoa huduma kama Kilimo Express (hypothetical) kwa chaguo nafuu kutoka mikoa mikuu ya kilimo. Daima thibitisha hali ya gari na sifa za dereva."
    else:
        return "Ushauri wa jumla wa usafirishaji: Ufungashaji sahihi na usafirishaji wa haraka ni muhimu kupunguza hasara baada ya mavuno. Tafuta vituo vya kupoezea kwa bidhaa zinazoharibika."

# --- Gemini AI Integration ---
def get_gemini_advice(user_prompt: str, lang: str = "sw") -> str:
    """Sends a prompt to Gemini AI and returns the generated text."""
    if not gemini_model:
        return "Samahani, huduma ya ushauri wa AI haipatikani kwa sasa. Tafadhali jaribu tena baadaye." if lang == "sw" else "Sorry, AI advisory service is currently unavailable. Please try again later."
    try:
        # Define the system instruction/role for Gemini
        system_instruction = (
     "Wewe ni Kilimo Smart, mtaalamu mzoefu wa kilimo na bustani nchini Tanzania. "
     "Lengo lako kuu ni kutoa ushauri sahihi, wa kutekelezeka, na unaoeleweka kwa urahisi kwa wakulima wadogo wadogo kupitia ujumbe mfupi wa SMS. "
     "Majibu yako yote lazima yawe kwa Kiswahili fasaha, sanifu, na yenye lugha rahisi kueleweka. "
     "Zingatia mazingira halisi ya kilimo Tanzania, ikiwemo aina za mazao (mfano: mahindi, maharage, mpunga, nyanya, viazi, pamba), hali ya hewa ya maeneo mbalimbali, aina za udongo, na changamoto za kawaida wanazokumbana nazo wakulima (mfano: magonjwa, wadudu, ukame, mafuriko, masoko). "
     
     # Nyongeza: Ushauri wa Hali ya Hewa
     "Jumuisha ushauri wa hali ya hewa unaofaa kwa kilimo (mfano: maandalizi ya mvua, ukame, joto kali) na umuhimu wa kufuatilia utabiri wa hali ya hewa kwa eneo lao. Waelekeze wachukue hatua stahiki kulingana na hali ya hewa inayotarajiwa. "
     
     # Nyongeza: Akili ya Biashara na Lini Kuuza
     "Toa ushauri wa masoko na wakati sahihi wa kuuza mazao kulingana na mwenendo wa soko, mahitaji ya msimu, na utabiri wa bei wa jumla. Himiza mkulima kuzingatia taarifa za soko kabla ya kuuza. "
     
     # Nyongeza: Faida (bila kukokotoa namba kamili)
     "Kuhusu faida, eleza umuhimu wa kufanya hesabu ya gharama za uzalishaji (mbegu, mbolea, kazi) na bei ya kuuza. Usijaribu kukokotoa faida halisi kwa namba kwani huna taarifa zote za gharama za mkulima. Badala yake, eleza mambo muhimu ya kuzingatia ili kuongeza faida (mfano: kupunguza taka, kuboresha ubora wa mazao, kuuza wakati sahihi). "
     
     "Toa ushauri unaozingatia kanuni bora za kilimo kama vile: "
     "- Maandalizi ya shamba na udongo "
     "- Uchaguzi sahihi wa mbegu au miche "
     "- Mbinu za kupanda na nafasi "
     "- Usimamizi wa maji (umwagiliaji na mifereji) "
     "- Udhibiti wa magugu, wadudu, na magonjwa (njia za asili na salama) "
     "- Lishe ya mimea (mbolea za asili na za viwandani, matumizi sahihi) "
     "- Uvunaji na uhifadhi wa mazao "
     "- Masoko na bei (toa taarifa za jumla, usijaribu kubashiri bei halisi, himiza utafiti binafsi) " # Nimeongeza msisitizo wa utafiti binafsi
     "- Kilimo mseto na mzunguko wa mazao. "
     
     "Jibu moja kwa moja swali la mkulima kwa sauti inayosaidia na yenye kutia moyo. "
     "Ikiwa zao au eneo maalum limetajwa, tengeneza ushauri kulingana na hilo. "
     "Epuka majibu marefu sana kwa SMS. Kama jibu linahitaji maelezo zaidi, sema kwamba unaweza kutoa maelezo ya ziada ukishauriwa 'endelea'. "
     "Iwapo swali halihusiani na kilimo au haliwezekani kujibiwa, jibu kwa heshima na ueleze kuwa huduma yako ni kwa ajili ya ushauri wa kilimo pekee. "
     "Usijaribu kubashiri au kutoa taarifa zisizo na uhakika, hasa kuhusu bei halisi za soko au utabiri wa hali ya hewa wa muda mrefu sana bila data sahihi. Badala yake, eleza kuwa unatoa ushauri wa jumla au unashauri kutafuta taarifa za soko kutoka vyanzo rasmi. "
     "Daima kamilisha jibu lako kwa kuhamasisha mkulima na kumtia moyo. "
     "Ukihitajika, uliza maswali ya ufafanuzi ili kutoa ushauri sahihi zaidi." )
        
        # Using generate_content directly with system_instruction
        response = gemini_model.generate_content(
            user_prompt,
            safety_settings={
                "HARASSMENT": "block_none",
                "HATE": "block_none",
                "SEXUAL": "block_none",
                "DANGEROUS": "block_none",
            },
            system_instruction=system_instruction
        )
        
        # Check if parts exist and join them, otherwise return an empty string or default message
        if response.candidates and response.candidates[0].content.parts:
            return "".join(part.text for part in response.candidates[0].content.parts)
        else:
            logger.warning(f"Gemini response has no content parts: {response}")
            return "Samahani, sikuweza kutoa ushauri kwa ombi hilo. Jibu la AI lilikuwa tupu." if lang == "sw" else "Sorry, I couldn't generate advice for that request. The AI response was empty."
    except Exception as e:
        logger.error(f"Error calling Gemini API: {e}")
        return "Samahani, kuna tatizo la kiufundi na huduma ya ushauri. Tafadhali jaribu tena baadae." if lang == "sw" else "Sorry, there's a technical issue with the advisory service. Please try again later."

# --- General SMS sending function (used by both USSD and SMS routes) ---
def send_sms(phone: str, message: str) -> Dict[str, bool | str]:
    """Sends an SMS message via Africa's Talking."""
    try:
        # Africa's Talking expects numbers in international format
        if not phone.startswith('+'):
            phone = f"+255{phone.lstrip('0')}" # Assuming Tanzanian numbers if not international
        
        # Use AT_SHORTCODE if available, otherwise Africa's Talking will use a default sender ID.
        if AT_SHORTCODE:
            response = sms.send(message, [phone], AT_SHORTCODE)
        else:
            response = sms.send(message, [phone])
            
        logger.info(f"SMS sent to {phone}. Response: {response}")
        return {"success": True, "response": str(response)}
    except Exception as e:
        logger.error(f"SMS sending failed to {phone}: {str(e)}")
        return {"success": False, "error": str(e)}

# --- Web UI Endpoint (Optional: for testing or admin interface) ---
@app.route("/", methods=['GET'])
def home():
    """Renders a simple home page for information."""
    return f"<h1>Kilimo Smart Agricultural Advisory System</h1><p>This system provides agricultural advice via USSD and SMS. Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S EAT')}</p>"

# --- Africa's Talking SMS Webhook Endpoint ---
@app.route("/sms", methods=['POST'])
def sms_chatbot():
    """Handles incoming SMS messages from Africa's Talking for a free-form chatbot."""
    sender = request.form.get("from")
    message_text = request.form.get("text")

    logger.info(f"Received SMS from {sender}: {message_text}")

    if not sender or not message_text:
        logger.error("Missing 'from' or 'text' in SMS webhook request.")
        return jsonify({"status": "error", "message": "Missing parameters"}), 400

    session_id = sender # Phone number is the session ID for SMS
    user_message_normalized = message_text.lower().strip()
    
    # Initialize conversation if new session or user types "hi", "habari", "mambo"
    # This block handles starting a new conversation or resetting an existing one.
    if session_id not in sms_conversations or user_message_normalized in ["hi", "habari", "mambo"]:
        sms_conversations[session_id] = [] # Start a fresh history
        initial_prompt = (
            "Karibu Kilimo Smart! Mimi ni mtaalamu wako wa kilimo kupitia SMS. "
            "Unaweza kuniuliza chochote kuhusu kilimo, mfano: 'Nipe ushauri wa kulima mahindi.' au 'exit' kuacha."
        )
        send_sms(sender, initial_prompt)
        logger.info(f"New SMS session started for {sender}. Initial message sent.")
        return jsonify({"status": "ok"})

    # Retrieve current conversation history for the sender
    history = sms_conversations.get(session_id, []) 
    reply_to_send = ""

    # Handle 'exit' command to end the conversation
    if user_message_normalized == "exit":
        reply_to_send = "Asante kwa kutumia huduma ya Kilimo Smart. Kwaheri!"
        send_sms(sender, reply_to_send)
        if session_id in sms_conversations:
            del sms_conversations[session_id] # Clear history for this session
        logger.info(f"SMS session for {sender} ended.")
        return jsonify({"status": "ok"})

    # Add the user's current message to the conversation history
    # This prepares the history to be sent to Gemini for context.
    history.append({"role": "user", "parts": [user_message_normalized]})

    # Check if the Gemini model is initialized and available
    if gemini_model:
        try:
            # Prepare history for Gemini API.
            # It needs to be in the format: [{"role": "user", "parts": [...]}, {"role": "model", "parts": [...]}, ...]
            gemini_formatted_history = []
            for msg in history:
                if msg.get("role") == "user":
                    gemini_formatted_history.append({"role": "user", "parts": msg["parts"]})
                elif msg.get("role") == "model":
                    gemini_formatted_history.append({"role": "model", "parts": msg["parts"]})

            # Start a chat session with the prepared history.
            # IMPORTANT: The 'system_instruction' is now set during 'GenerativeModel' initialization,
            # so it is NOT passed here. This resolves the "unexpected keyword argument" error.
            chat_session = gemini_model.start_chat(
                history=gemini_formatted_history[:-1] # Exclude the current user message as send_message adds it
            ) 
            
            # Send the user's latest message to the Gemini chat session
            response_from_gemini = chat_session.send_message(user_message_normalized)
            reply_to_send = response_from_gemini.text
            
            # Add Gemini's response to the history for future turns
            history.append({"role": "model", "parts": [reply_to_send]})
            sms_conversations[session_id] = history # Update the in-memory session history
            
        except Exception as e:
            logger.error(f"Gemini API call failed for SMS: {e}")
            reply_to_send = "Samahani, kumetokea hitilafu wakati wa kutafuta taarifa. Tafadhali jaribu tena."
            history.append({"role": "model", "parts": [reply_to_send]}) # Store error in history
            sms_conversations[session_id] = history 
    else:
        # Fallback if Gemini model failed to initialize
        reply_to_send = "Samahani, huduma ya maelezo ya kilimo haipatikani kwa sasa. Jaribu tena baada ya muda mfupi."
        history.append({"role": "model", "parts": [reply_to_send]}) # Store fallback in history
        sms_conversations[session_id] = history 

    # Send the final reply back to the user via SMS
    send_sms(sender, reply_to_send)

    return jsonify({"status": "ok"})

# --- Africa's Talking USSD Webhook Endpoint ---
@app.route('/ussd', methods=['POST', 'GET'])
def agricultural_ussd():
    """Handles incoming USSD requests for the agricultural advisory system."""
    session_id = request.values.get("sessionId", "")
    service_code = request.values.get("serviceCode", "")
    phone_number = request.values.get("phoneNumber", "")
    text = request.values.get("text", "").strip()

    logger.info(f"USSD Request - SessionID: {session_id}, Phone: {phone_number}, Text: '{text}'")

    response_text = ""
    
    # Initialize or retrieve USSD session
    if session_id not in ussd_sessions:
        ussd_sessions[session_id] = {
            "phone_number": phone_number,
            "current_step": "welcome",
            "data": {}, # To store any temporary data like selected crop
            "last_active": datetime.now()
        }
    
    session_data = ussd_sessions[session_id]
    session_data["last_active"] = datetime.now() # Update activity timestamp

    # USSD menu flow logic
    # The 'text' variable holds the entire input string so far (e.g., "1*2", "1")
    input_parts = text.split('*')
    current_input = input_parts[-1] if text else "default" # Get the last entered part
    
    current_step = session_data["current_step"]

    if current_step == "welcome" and current_input == "default":
        response_text = "CON Karibu Kilimo Smart! Chagua Huduma:\n" \
                       "1. Bei za Mazao\n" \
                       "2. Ushauri wa Kilimo (AI)\n" \
                       "3. Tahadhari ya Hali ya Hewa\n" \
                       "4. Taarifa za Uhifadhi na Usafirishaji\n" \
                       "5. Toka"
        session_data["current_step"] = "main_menu_choice"
    
    elif current_step == "main_menu_choice":
        if current_input == "1":
            response_text = "CON Chagua Zao:\n" \
                           "1. Mahindi\n" \
                           "2. Maharage\n" \
                           "3. Mchele\n" \
                           "4. Nyanya\n" \
                           "99. Rudi Menu Kuu"
            session_data["current_step"] = "crop_price_choice"
        elif current_input == "2":
            response_text = "CON Andika swali lako la kilimo:\n" \
                           "(Mfano: Nipe ushauri wa kulima mahindi mkoani Morogoro?)\n" \
                           "99. Rudi Menu Kuu"
            session_data["current_step"] = "ai_advice_query"
        elif current_input == "3":
            weather_data = get_current_weather()
            if weather_data:
                response_text = (f"END Hali ya hewa {weather_data['city']}:\n"
                                 f"Joto: {weather_data['temperature']}°C, Unyevunyevu: {weather_data['humidity']}%\n"
                                 f"Maelezo: {weather_data['description']}\n"
                                 f"Upepo: {weather_data['wind_speed']} m/s\n"
                                 f"Utabiri: {weather_data['forecast_summary']}")
            else:
                response_text = "END Samahani, imeshindikana kupata taarifa za hali ya hewa kwa sasa."
            # Session ends after providing weather info
            if session_id in ussd_sessions:
                del ussd_sessions[session_id]
        elif current_input == "4":
            response_text = "CON Chagua aina ya Taarifa:\n" \
                           "1. Uhifadhi wa Nafaka\n" \
                           "2. Usafirishaji\n" \
                           "99. Rudi Menu Kuu"
            session_data["current_step"] = "logistics_info_choice"
        elif current_input == "5":
            response_text = "END Asante kwa kutumia Kilimo Smart! Kwaheri."
            if session_id in ussd_sessions:
                del ussd_sessions[session_id]
        else:
            response_text = "CON Chaguo batili. Tafadhali chagua tena:\n" \
                           "1. Bei za Mazao\n" \
                           "2. Ushauri wa Kilimo (AI)\n" \
                           "3. Tahadhari ya Hali ya Hewa\n" \
                           "4. Taarifa za Uhifadhi na Usafirishaji\n" \
                           "5. Toka"
            # Stay in current step
    
    elif current_step == "crop_price_choice":
        if current_input == "99": # Back to main menu
            response_text = "CON Karibu Kilimo Smart! Chagua Huduma:\n" \
                           "1. Bei za Mazao\n" \
                           "2. Ushauri wa Kilimo (AI)\n" \
                           "3. Tahadhari ya Hali ya Hewa\n" \
                           "4. Taarifa za Uhifadhi na Usafirishaji\n" \
                           "5. Toka"
            session_data["current_step"] = "main_menu_choice"
        else:
            crop_map = {'1': 'Maize', '2': 'Beans', '3': 'Rice', '4': 'Tomato'}
            selected_crop = crop_map.get(current_input)
            if selected_crop:
                price_info = get_crop_prices(selected_crop, region="Dar es Salaam") # Default region
                forecast_info = get_crop_price_forecast(selected_crop)
                response_text = f"END {price_info}\n{forecast_info}\nAsante kwa kutumia Kilimo Smart!"
                if session_id in ussd_sessions:
                    del ussd_sessions[session_id] # End session
            else:
                response_text = "CON Chaguo batili. Tafadhali chagua zao:\n" \
                               "1. Mahindi\n" \
                               "2. Maharage\n" \
                               "3. Mchele\n" \
                               "4. Nyanya\n" \
                               "99. Rudi Menu Kuu"
                # Stay in current step
    
    elif current_step == "ai_advice_query":
        if current_input == "99": # Back to main menu
            response_text = "CON Karibu Kilimo Smart! Chagua Huduma:\n" \
                           "1. Bei za Mazao\n" \
                           "2. Ushauri wa Kilimo (AI)\n" \
                           "3. Tahadhari ya Hali ya Hewa\n" \
                           "4. Taarifa za Uhifadhi na Usafirishaji\n" \
                           "5. Toka"
            session_data["current_step"] = "main_menu_choice"
        else:
            user_query = text.split('*', 1)[1] if '*' in text else text # Get the actual query
            if user_query.strip():
                ai_response = get_gemini_advice(user_query, lang="sw")
                response_text = f"END {ai_response}\nAsante kwa kutumia Kilimo Smart!"
                if session_id in ussd_sessions:
                    del ussd_sessions[session_id] # End session
            else:
                response_text = "CON Tafadhali ingiza swali lako la kilimo:\n" \
                               "(Mfano: Nipe ushauri wa kulima mahindi mkoani Morogoro?)\n" \
                               "99. Rudi Menu Kuu"
                # Stay in current step

    elif current_step == "logistics_info_choice":
        if current_input == "99": # Back to main menu
            response_text = "CON Karibu Kilimo Smart! Chagua Huduma:\n" \
                           "1. Bei za Mazao\n" \
                           "2. Ushauri wa Kilimo (AI)\n" \
                           "3. Tahadhari ya Hali ya Hewa\n" \
                           "4. Taarifa za Uhifadhi na Usafirishaji\n" \
                           "5. Toka"
            session_data["current_step"] = "main_menu_choice"
        else:
            if current_input == "1":
                info = get_logistics_info("grain storage")
                response_text = f"END {info}\nAsante kwa kutumia Kilimo Smart!"
                if session_id in ussd_sessions:
                    del ussd_sessions[session_id] # End session
            elif current_input == "2":
                info = get_logistics_info("transport")
                response_text = f"END {info}\nAsante kwa kutumia Kilimo Smart!"
                if session_id in ussd_sessions:
                    del ussd_sessions[session_id] # End session
            else:
                response_text = "CON Chaguo batili. Tafadhali chagua:\n" \
                               "1. Uhifadhi wa Nafaka\n" \
                               "2. Usafirishaji\n" \
                               "99. Rudi Menu Kuu"
                # Stay in current step
    
    else:
        # Fallback for unexpected states or direct default for `text` (if the session somehow got out of sync)
        response_text = "END Samahani, kuna tatizo. Tafadhali anza tena."
        if session_id in ussd_sessions:
            del ussd_sessions[session_id] # Clear session for clean restart


    logger.info(f"USSD Response - SessionID: {session_id}, Response: '{response_text}'")
    return make_response(response_text, 200, {'Content-Type': 'text/plain'})


# --- Background task for cleaning up old sessions (simplified for in-memory) ---
# In a real production app, this would be a scheduled task or handled by Redis's TTL.
def cleanup_old_sessions():
    """Removes sessions that have exceeded the SESSION_TIMEOUT."""
    current_time = datetime.now()
    sessions_to_delete = []

    # Clean up USSD sessions
    for session_id, session_info in ussd_sessions.items():
        if current_time - session_info["last_active"] > SESSION_TIMEOUT:
            sessions_to_delete.append(session_id)
    
    for session_id in sessions_to_delete:
        del ussd_sessions[session_id]
        logger.info(f"Cleaned up old USSD session: {session_id}")
    
    # SMS sessions usually end with 'exit' but can also be timed out if needed
    # (Less critical for SMS as it's more conversational, but can be added if memory becomes an issue)
    # sms_sessions_to_delete = []
    # for session_id, history in sms_conversations.items():
    #     # Assuming last item in history has a timestamp or similar for last activity
    #     # Or you could add a 'last_active' timestamp directly to the sms_conversations dict
    #     # For this structure, we'll keep SMS sessions until 'exit' or server restart
    #     pass


# --- Application Entry Point ---
if __name__ == '__main__':
    # It's good practice to get the port from environment variables, common for deployment platforms
    # In a production environment, use a WSGI server like Gunicorn or uWSGI (e.g., gunicorn app:app)
    # The 'debug=False' is important for production.
    logger.info("Starting Kilimo Smart Agricultural Advisory System...")
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))