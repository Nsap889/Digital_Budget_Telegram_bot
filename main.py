import telebot
import datetime
import requests
import json
import pandas as pd
import openpyxl
import numpy as np
import time
import io
import matplotlib
import pyodbc
import re
from telebot import apihelper
import os
import threading
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.exceptions import InsecureRequestWarning
import warnings
import Dict_names
import config
import AI


# Отключаем предупреждения о SSL
warnings.filterwarnings('ignore', category=InsecureRequestWarning)

# Глобальный флаг занятости
is_processing = False
processing_lock = threading.Lock()

# Устанавливаем переменные окружения ДО ВСЕГО
os.environ['TELEBOT_READ_TIMEOUT'] = '120'
os.environ['TELEBOT_CONNECT_TIMEOUT'] = '120'

# Настраиваем таймауты
apihelper.READ_TIMEOUT = 150
apihelper.CONNECT_TIMEOUT = 150

print(f"Таймауты установлены: READ={apihelper.READ_TIMEOUT}, CONNECT={apihelper.CONNECT_TIMEOUT}")


# Настройка сессии с повторными попытками для requests
def create_session_with_retries():
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST", "PUT", "DELETE"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.timeout = (30, 120)  # (connect, read)
    return session


# Создаем глобальную сессию
requests_session = create_session_with_retries()

pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)

# token DB
token=config.DIGITAL_BUDGET_TOKEN
# Токен tg_бота
bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN)


# Отправка сообщений в другой тг бот информатор
def send_msg(text):
    for attempt in range(3):
        try:
            token_bot = config.TELEGRAM_BOT_TOKEN_ACCOINT_FOR_LOGGS
            chat_id = config.TELEGRAM_BOT_TOKEN_ACCOINT_FOR_LOGGS_CHANNEL
            url_req = f"https://api.telegram.org/bot{token_bot}/sendMessage"
            response = requests_session.get(url_req, params={"chat_id": chat_id, "text": text})
            results = response.json()
            print(results['result']['text'])
            return
        except Exception as e:
            print(f"Ошибка отправки сообщения (попытка {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(2)
            else:
                print(f"Не удалось отправить сообщение: {text}")


count = 0

# Глобальные переменные
Sites_list = []
result = pd.DataFrame(
    columns=['Site', 'Category', 'advertiser', 'siteStatus', 'isVerify',
             'Date', 'kind', 'budget', 'visits', 'type'])


# Подключение к SQL с обработкой ошибок
def get_sql_connection():
    for attempt in range(3):
        try:
            cnxn = pyodbc.connect(
                f"Driver={config.SQL_DRIVER};"
                f"Server={config.SQL_SERVER};"
                f"Database={config.SQL_DATABASE};"
                f"Trusted_Connection=yes;",
                timeout=30
            )
            return cnxn
        except Exception as e:
            print(f"Ошибка подключения к SQL (попытка {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(2)
            else:
                raise
    return None


def sql_dates(request):
    cnxn = None
    try:
        cnxn = get_sql_connection()
        if cnxn:
            cursor = cnxn.cursor()
            cursor.execute(request)
            cursor.commit()
            cursor.close()
    except Exception as e:
        print(f"Ошибка выполнения SQL: {e}")
    finally:
        if cnxn:
            cnxn.close()


# Обработчик команды /start
@bot.message_handler(commands=['start'])
def start_command(message):
    bot.send_message(message.chat.id, "👋 Привет! Я бот для выгрузки данных из Digital Budget \n"
                                      "Перечислите нужные сайты через запятую, пример: nike.com,reebok.com,adidas.com \n"
                                      "(Без - 'https:/', 'www.', '/') - Только основной домен \n"
                                      "Примеры: \n"
                                      "https://www.pfizer.ru/products/allproducts/diflucan -> pfizer.ru   \n"
                                      "www.montavit.ru/montavit-gel/ -> montavit.ru \n"
                                      "Тех поддержка: @sna17777 \n")


def invalid_symbols(spisok: list) -> bool:
    for element in spisok:
        if re.search(r"www|http|https|/", element, re.I) or not re.search(r"\.", element, re.I):
            print(f'Невалидный сайт - {element}')
            return False
        else:
            print(f'{element} - ok')
    return True


def ad_site(site: str, arg=None, max_retries=3):
    """Создание сайта с повторными попытками"""
    base_url = "https://lk-v3.digitalbudget.ru"
    endpoint = f"{base_url}/company/site"

    payload = {
        "domain": site,
        "category": arg,
        "advertiser": None
    }

    headers = {
        "app-token": token,
        "Content-Type": "application/json"
    }

    for attempt in range(max_retries):
        try:
            response = requests_session.post(
                url=endpoint,
                json=payload,
                headers=headers,
                timeout=(30, 60)
            )

            print(site, datetime.datetime.now(), f"Status Code: {response.status_code}")

            if response.status_code == 201:
                print(site, "✅ Сайт успешно создан!")
                return response.status_code
            elif response.status_code == 200:
                print(site, "- Сайт уже существует в ЛК")
                return response.status_code
            elif response.status_code == 400:
                print(site, "❌ Ошибка 400: невалидный домен или категория")
                return response.status_code
            elif response.status_code == 401:
                print(site, "❌ Ошибка 401: Не авторизован")
                return response.status_code
            else:
                print(f"Неожиданный статус: {response.status_code}")
                return response.status_code

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            print(site, f"❌ Ошибка соединения (попытка {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(3 * (attempt + 1))
            else:
                return 500
        except Exception as e:
            print(site, f"❌ Ошибка: {e}")
            return 500
    return 500


def DB_data(site: str, max_retries=3):
    """Получение данных с повторными попытками"""
    global result, count

    params = {
        "domain": site,
        "langValue": "ru",
        "createVerifyIfExistValue": "false"
    }

    headers = {"app-token": token}

    for attempt in range(max_retries):
        try:
            resp = requests_session.get(
                url='https://lk-v3.digitalbudget.ru/v2/reports/monthly',
                headers=headers,
                params=params,
                timeout=(30, 90)
            )

            data = resp.json()

            if data['success'] is True:
                print(site, '- такой сайт есть в ЛК - выгружаю его')

                for cnt, i in enumerate(data['data']['date']):
                    for rrr in range(0, len(data['data']['date'][cnt]['data'])):
                        result.loc[count, 'Site'] = data['data']['site']
                        result.loc[count, 'Category'] = data['data']['category']
                        result.loc[count, 'advertiser'] = data['data']['advertiser']
                        result.loc[count, 'siteStatus'] = data['data']['siteStatus']
                        result.loc[count, 'isVerify'] = data['data']['isVerify']
                        result.loc[count, 'Date'] = i['date'][:]
                        result.loc[count, 'kind'] = i['data'][rrr]['kind']
                        result.loc[count, 'budget'] = i['data'][rrr]['budget']
                        result.loc[count, 'visits'] = i['data'][rrr]['visits']
                        result.loc[count, 'type'] = i['data'][rrr]['type']
                        count = count + 1
                return True

            elif data['success'] is False:
                print(site, '- сайта нет в ЛК, создаю...')
                ad_site(site, "БЕЗ КАТЕГОРИИ")
                time.sleep(3)
                return DB_data(site, max_retries)

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            print(site, f"❌ Ошибка соединения (попытка {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(3 * (attempt + 1))
            else:
                print(site, "- Не удалось получить данные после всех попыток")
                return False
        except json.JSONDecodeError as e:
            print(site, f"❌ Ошибка парсинга JSON: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                return False
        except Exception as e:
            print(site, f"❌ Неожиданная ошибка: {e}")
            return False
    return False


@bot.message_handler(content_types=['text'])
def handle_text(message):

    # РЕЖИМ ТЕХНИЧЕСКИХ РАБОТ // ЗДЕСЬ ВКЛЮЧАЕМ РЕЖИМ ПРОФИЛАКТИЧЕСКИХ РАБОТ !!!
    MAINTENANCE_MODE = False

    if MAINTENANCE_MODE:
        bot.send_message(
            message.chat.id,
            "🛠 Уважаемые пользователи!\n\n"
            "В настоящее время ведутся профилактические работы.\n"
            "Сервис временно недоступен.\n\n"
            "Приносим извинения за неудобства!"
        )
        return

    global Sites_list, result, is_processing, count

    with processing_lock:
        if is_processing:
            bot.send_message(
                message.chat.id,
                "⏳ Бот сейчас обрабатывает другой запрос. Пожалуйста, подождите немного и повторите через минуту."
            )
            return
        is_processing = True

    try:
        Sites_list="".join((message.text).lower().split()).replace('https://','').replace('http://','').replace('www.','').replace('www2','').split(',')
        Sites_list = list(set(Sites_list))
        if '' in Sites_list:
            Sites_list.remove('')
        Sites_list=[i for i in Sites_list if '.' in i] #


        validation = invalid_symbols(Sites_list)
        if validation:
            bot.send_message(message.chat.id, '✅ Валидация сайтов пройдена')

            if message.chat.id in Dict_names.Names.keys():

                #Уведомление Пользователю кот выгружает:
                bot.send_message(message.chat.id, '✅ Авторизация пройдена')
                bot.send_message(message.chat.id, f'📋 Список уникальных сайтов для выгрузки - {Sites_list}')
                bot.send_message(message.chat.id, f'🔢 Количество уникальных сайтов на входе - {len(Sites_list)}')

                #Инсертим запрос в БД 39 сервер
                Sql_request = f"""SET NOCOUNT ON
                                insert into nsaprykin.telegram_bot_db values ({int(message.chat.id)},'{str((message.text))}','{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}')
                                commit"""


                sql_dates(Sql_request)
                send_msg(f"Digital_Budget - User: {Dict_names.Names[(message.chat.id)]}, Request: {str(message.text)}, Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

                def louncher():
                    for index_site, site in enumerate(Sites_list):
                        try:
                            bot.send_message(message.chat.id, f'📊 Выгружаю - {site}')
                            ad_site(site)
                            DB_data(site)
                            print('--------------------')
                        except Exception as e:
                            bot.send_message(message.chat.id, f'❌ Ошибка при обработке {site}: {str(e)[:100]}')
                            print(f'Ошибка при обработке {site}: {e}')

                louncher()

                bot.send_message(message.chat.id, f'📈 Количество сайтов на выходе - {result["Site"].nunique()}')

                for i in Sites_list:
                    if i in result['Site'].unique():
                        print(f'{i} - выгружен')
                    else:
                        print(f'{i} - не выгружен')

                bot.send_message(message.chat.id, '💾 Сохраняю Excel... Пару минут')

                #папка куда склыдываются отчеты если не вышел ексель
                folder_path = 'X:/#Teams/Analytics/ResearchDocs/Insider_Data/NsapRequests/Digital_Budget_Reports'
                os.makedirs(folder_path, exist_ok=True)
                file_path = os.path.join(folder_path,
                                         f'Report_{datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}.xlsx')
                result['Year'] = result['Date'].str[:4]
                result['Month'] = result['Date'].str[5:7].astype(int)
                result.to_excel(file_path, index=False)
                bot.send_message(message.chat.id, f'📁 Файл сохранен: {file_path}')

                #df = pd.read_excel(result)  # ваши данные
                #DataFrame_input(df, output_path="analytics_report.pptx")



                buffer = io.BytesIO()
                try:
                    result.to_excel(buffer, index=False, engine='openpyxl')
                    buffer.seek(0)

                    bot.send_document(
                        message.chat.id,
                        document=buffer,
                        timeout=180,
                        visible_file_name='report.xlsx'
                    )
                except Exception as e:
                    print(f"Ошибка отправки документа: {e}")
                    bot.reply_to(message, "Файл посчитан, но не могу загрузить его на сервер Telegram")
                finally:
                    buffer.close()
                    result = pd.DataFrame(columns=result.columns)
                    count = 0
            else:
                bot.send_message(message.chat.id,
                                 '⛔ У вас нет прав для выгрузки. Пришлите свой Telegram ID (@Getmyid_bot) -> @sna17777')
        else:
            bot.send_message(message.chat.id, '🔧 Проверьте..Возможно неправильно указан сайт...Только основной домен...без "https://"  \nПопробуйте еще раз')
    except Exception as e:
        print(f"Критическая ошибка в handle_text: {e}")
        bot.send_message(message.chat.id, f'💥 Произошла ошибка: {str(e)[:200]}')
    finally:
        with processing_lock:
            is_processing = False


if __name__ == '__main__':
    print("🚀 Бот запущен...")

    while True:
        try:
            bot.polling(none_stop=True, interval=1, timeout=120)
        except requests.exceptions.ConnectionError as e:
            print(f"💥 Ошибка соединения: {e}")
            print("🔄 Перезапуск через 10 секунд...")
            time.sleep(10)
            # Пересоздаем сессию
            requests_session.close()
            requests_session = create_session_with_retries()
            continue
        except Exception as e:
            print(f"💥 Бот упал с ошибкой: {e}")
            print("🔄 Перезапуск через 5 секунд...")
            print(f"Тип ошибки: {type(e).__name__}")
            print(f"Текст ошибки: {str(e)}")
            print(f"Полная информация: {repr(e)}")
            time.sleep(5)
            continue
        break