#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json,requests,urllib
import ConfigParser
import pprint,datetime
from collections import Counter
import pandas
import numpy
import slackweb

# 外部設定ファイル読み込み
INIFILE = ConfigParser.SafeConfigParser()
INIFILE.read('/opt/script/.auth_info')

# 変数定義
API_VER = INIFILE.get('Azure', 'AZURE_API_VER')
TENANT_ID = INIFILE.get('Azure', 'AZURE_TENANT_ID')
CLIENT_ID = INIFILE.get('Azure', 'AZURE_CLIENT_ID') 
CLIENT_PW = INIFILE.get('Azure', 'AZURE_CLIENT_PW') 
SUBSCRIPTIONS_ID = '966af054-0441-4ada-b5b3-aa522e9f9b43'
OFFER_DURABLE_ID = 'MS-AZR-0036P' # サブスクリプションに紐づく種別ID

# Slack認証設定
SLACK_WEBHOOK=INIFILE.get('slack', 'WEBHOOK_URL')
SLACK_CHANNEL='#intranet'
SLACK_USER='AzureBillingInfo'

# 昨日の日付を取得する
LAST_DAY = (datetime.date.today() -datetime.timedelta(1))
END_DATE = LAST_DAY.strftime('%Y-%m-%d')
START_DATE = (datetime.date(day=1, month=LAST_DAY.month, year=LAST_DAY.year)).strftime('%Y-%m-%d')


# AzureAPI AuthToken取得
def GetAuthToken():
    URL = "https://login.microsoftonline.com/" + TENANT_ID + "/oauth2/token?api-version=1.0"
    HEADER = {'Content-Type':'application/x-www-form-urlencoded'}
    DATA = {'client_id':CLIENT_ID,
            'client_secret':CLIENT_PW,
            'grant_type':'client_credentials',
            'resource':'https://management.azure.com/'}
    
    # AuthToken取得
    RESP = requests.post(
        URL,
        headers=HEADER,
        data=DATA)
    DEF_TOKEN = RESP.json().get('access_token')
    
    # DEF_TOKENを返す
    return DEF_TOKEN


# Azure利用データの取得(Azure Resource Usage API)
def GetAzureResourceUsage(DEF_SUBSCRIPTIONS_ID,DEF_AUTH_TOKEN,DEF_START_DATE,DEF_END_DATE):
    URL = "https://management.azure.com/subscriptions/" + DEF_SUBSCRIPTIONS_ID + "/providers/Microsoft.Commerce/UsageAggregates" + \
          "?api-version=" + API_VER + \
          "&reportedStartTime=" + DEF_START_DATE + \
          "&reportedEndTime=" + DEF_END_DATE + \
          "&showDetails=false"
    HEADER = {'Authorization':'Bearer ' + DEF_AUTH_TOKEN,'Content-Type':'application/json'}

    # 利用データの取得
    RESP = requests.get(
        URL,
        headers=HEADER)
    
    # 利用データをJSON形式で取得
    JSON_DATA = RESP.json().get('value')
    
    # AzureのRestAPIは、返り値が長いと途中で切られて’nextLink’から続きを取得する必要があるので、その処理
    while RESP.json().get('nextLink') != None:
        # URLを書き換える
        URL = RESP.json().get('nextLink')
    
        # 利用データの再取得
        RESP = requests.get(
            URL,
            headers=HEADER)
    
        # 再取得した利用データをJSON_DATAに追加
        JSON_DATA += RESP.json().get('value')
  
    # 必要となる情報('properties')のみを抽出する
    DATA = []
    for ATTR in JSON_DATA:
        DATA.append(ATTR.get('properties'))
     
    # DATAを返す
    return DATA


# Azure料金データの取得(Azure RateCard API)
def GetAzureRateCard(DEF_SUBSCRIPTIONS_ID,DEF_AUTH_TOKEN,DEF_OFFER_DURABLE_ID):
    # 取得する料金データのクエリ変数を定義(残念ながらJPYしか取れない・・・)
    DEF_CURRENCY = 'JPY'
    DEF_LOCALE = 'ja-JP'
    DEF_REGION = 'JP'
    
    # クエリ内容の記述
    SEARCH_QUERY = "OfferDurableId eq '" + DEF_OFFER_DURABLE_ID + "'" + \
                   " and Currency eq '" + DEF_CURRENCY  + "'" + \
                   " and Locale eq '" + DEF_LOCALE + "'" + \
                   " and RegionInfo eq '" + DEF_REGION + "'" 

    URL = "https://management.azure.com/subscriptions/" + DEF_SUBSCRIPTIONS_ID + "/providers/Microsoft.Commerce/RateCard" + \
          "?api-version=" + API_VER + "&$filter=" + urllib.quote(SEARCH_QUERY)
    HEADER = {'Authorization':'Bearer ' + DEF_AUTH_TOKEN,'Content-Type':'application/json'}
    
    RESP = requests.get(
        URL,
        headers=HEADER)
    DATA = RESP.json().get('Meters')
    return DATA


# Azure 利用データ(quantity)のMeterID別合計
def GetQuantitySum(IN_DATA):
    COUNTER_DATA = Counter()
    for VALUE in IN_DATA:
        COUNTER_DATA[VALUE['meterId']] += VALUE['quantity']
    DICT_DATA = dict()
    for k,v in dict(COUNTER_DATA).items():
        DICT_DATA[k] = dict({"quantity": v})
    return DICT_DATA


# Azure MaterID別のレートデータ
def GetRateData(IN_DATA):
    DICT_DATA = dict()
    for k in IN_DATA:
        DICT_DATA[k["MeterId"]] = dict({"MeterRates": k["MeterRates"]['0'],
                                        "MeterName": k["MeterName"],
                                        "MeterCategory": k["MeterCategory"],
                                        "MeterSubCategory": k["MeterSubCategory"]})
    return DICT_DATA


# RateとQuantityデータを結合する
def JoinQuantityAndRate(USAGE_DICT_DATA,RATE_DICT_DATA):
    DICT_DATA = dict()
    for k in USAGE_DICT_DATA:
        DICT_DATA[k] = dict({"quantity": USAGE_DICT_DATA[k]["quantity"],
                             "MeterRates": RATE_DICT_DATA[k]["MeterRates"],
                             "MeterName": RATE_DICT_DATA[k]["MeterName"],
                             "MeterFee": USAGE_DICT_DATA[k]["quantity"] * RATE_DICT_DATA[k]["MeterRates"],
                             "MeterCategory": RATE_DICT_DATA[k]["MeterCategory"],
                             "MeterSubCategory": RATE_DICT_DATA[k]["MeterSubCategory"]})
    return DICT_DATA


# カテゴリごとに利用金額(円)を合計する
def SumUsagefeeByCategory(IN_DATA):
    COUNTER_DATA = Counter()
    for k in IN_DATA:
        COUNTER_DATA[IN_DATA[k]['MeterCategory']] += IN_DATA[k]['MeterFee']
    DICT_DATA = dict()
    for k,v in dict(COUNTER_DATA).items():
        DICT_DATA[k] = int(v)
    return DICT_DATA


# 合計利用金額(円)を取得する
def SumUsagefee(IN_DATA):
    COUNTER_DATA = Counter()
    for k in IN_DATA:
        COUNTER_DATA['ALL'] += IN_DATA[k]['MeterFee']
    return int(COUNTER_DATA['ALL'])


# TOKENの取得 
TOKEN = GetAuthToken()

# 利用データの取得
USAGE_JSON = GetAzureResourceUsage(SUBSCRIPTIONS_ID,TOKEN,START_DATE,END_DATE)
USAGE_DICT = GetQuantitySum(USAGE_JSON)

# レートデータの取得
RATE_JSON = GetAzureRateCard(SUBSCRIPTIONS_ID,TOKEN,OFFER_DURABLE_ID)
RATE_DICT = GetRateData(RATE_JSON)

# データの結合・合計
USAGE_DATA = JoinQuantityAndRate(USAGE_DICT,RATE_DICT)
CATEGORY_SUM = SumUsagefeeByCategory(USAGE_DATA)
TOTAL_SUM = SumUsagefee(USAGE_DATA)

# ==== SlackへPostする ====
SLACK_TEXT='今月の、昨日までのAzureの利用料金は￥' + "{:,d}".format(TOTAL_SUM) + 'になります。\n ' + \
           '※ MSスポンサープランのため、無料チケット分は請求されていません。また、日本円計算のため実際の請求(ドル計算)とは差異が発生します。'

slack=slackweb.Slack(url=SLACK_WEBHOOK)
attachments=[]
attachment={'pretext': '各サービス別の利用料金','fields': []}

for k in CATEGORY_SUM:
    item={'title': k ,'value': '￥' + "{:,d}".format(CATEGORY_SUM[k]) ,'short': "true"}
    attachment['fields'].append(item)

attachments.append(attachment)
slack.notify(text=SLACK_TEXT, channel=SLACK_CHANNEL, username=SLACK_USER, icon_emoji=":azure-icon:", attachments=attachments)

