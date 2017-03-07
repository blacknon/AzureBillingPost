#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os,json,requests,urllib,pprint,datetime,pandas,numpy
import ConfigParser
from collections import Counter
import slackweb

iniFile = ConfigParser.SafeConfigParser()
iniFile.read(os.path.abspath(os.path.dirname(__file__)) + '/.auth_info')

# ==== Azure認証情報 ====
apiVer          = iniFile.get('Azure', 'AZURE_API_VER')
tenantID        = iniFile.get('Azure', 'AZURE_TENANT_ID')
clientID        = iniFile.get('Azure', 'AZURE_CLIENT_ID') 
clientPW        = iniFile.get('Azure', 'AZURE_CLIENT_PW') 
subscriptionsID = iniFile.get('Azure', 'AZURE_SUBSCRIPTIONS') 
offerDurableID  = 'MS-AZR-0036P' # サブスクリプションに紐づく種別ID

# ==== Slack認証設定 ====
slackWebhook = iniFile.get('slack', 'WEBHOOK_URL')
slackChannel = iniFile.get('slack', 'CHANNEL')
slackUser    = iniFile.get('slack', 'USER')

# ==== 昨日の日付を取得する ====
lastDay = (datetime.date.today() -datetime.timedelta(1))
lastDayStr = lastDay.strftime('%Y-%m-%d')

# ==== 計算開始日を先月末の日付とする ====
lastMonth   = lastDay - datetime.timedelta(days=lastDay.day)
startDayStr = lastMonth.strftime('%Y-%m-%d')


# AzureAPI AuthToken取得
def GetAuthToken():
    url = "https://login.microsoftonline.com/" + tenantID + "/oauth2/token?api-version=1.0"
    header = {'Content-Type':'application/x-www-form-urlencoded'}
    data = {'client_id':clientID,
            'client_secret':clientPW,
            'grant_type':'client_credentials',
            'resource':'https://management.azure.com/'}
    
    # AuthToken取得
    resp = requests.post(
        url,
        headers=header,
        data=data)

    defToken = resp.json().get('access_token')    
    return defToken


# Azure利用データの取得(Azure Resource Usage API)
def GetAzureResourceUsage(defSubscriptionsID,defAuthToken,defStartDay,defEndDay):
    url = "https://management.azure.com/subscriptions/" + defSubscriptionsID + "/providers/Microsoft.Commerce/UsageAggregates" + \
          "?api-version=" + apiVer + \
          "&reportedStartTime=" + defStartDay + \
          "&reportedEndTime=" + defEndDay + \
          "&showDetails=false"
    header = {'Authorization':'Bearer ' + defAuthToken,'Content-Type':'application/json'}

    # 利用データの取得
    resp = requests.get(
        url,
        headers=header)
    
    # 利用データをJSON形式で取得
    jsonData = resp.json().get('value')
    
    # AzureのRestAPIは、返り値が長いと途中で切られて’nextLink’から続きを取得する必要があるので、その処理
    while resp.json().get('nextLink') != None:
        # URLを書き換える
        url = resp.json().get('nextLink')
    
        # 利用データの再取得
        resp = requests.get(
            url,
            headers=header)
    
        # 再取得した利用データをJSON_DATAに追加
        jsonData += resp.json().get('value')
  
    # 必要となる情報('properties')のみを抽出する
    data = []
    for attr in jsonData:
        data.append(attr.get('properties'))
     
    # DATAを返す
    return data


# Azure料金データの取得(Azure RateCard API)
def GetAzureRateCard(defSubscriptionsID,defAuthToken,defOfferDurableID):
    # 取得する料金データのクエリ変数を定義(残念ながらJPYしか取れない・・・)
    defCurrency = 'JPY'
    defLocale = 'ja-JP'
    defRegion = 'JP'
    
    # クエリ内容の記述
    searchQuery = "OfferDurableId eq '" + defOfferDurableID + "'" + \
                   " and Currency eq '" + defCurrency + "'" + \
                   " and Locale eq '" + defLocale + "'" + \
                   " and RegionInfo eq '" + defRegion + "'" 

    url = "https://management.azure.com/subscriptions/" + defSubscriptionsID + "/providers/Microsoft.Commerce/RateCard" + \
          "?api-version=" + apiVer + "&$filter=" + urllib.quote(searchQuery)
    header = {'Authorization':'Bearer ' + defAuthToken,'Content-Type':'application/json'}
    
    resp = requests.get(
        url,
        headers=header)
    data = resp.json().get('Meters')
    return data


# Azure 利用データ(quantity)のMeterID別合計
def GetQuantitySum(inData):
    counterData = Counter()
    for val in inData:
        counterData[val['meterId']] += val['quantity']
    dictData = dict()
    for k,v in dict(counterData).items():
        dictData[k] = dict({"quantity": v})
    return dictData


# Azure MaterID別のレートデータ
def GetRateData(inData):
    dictData = dict()
    for k in inData:
        dictData[k["MeterId"]] = dict({"MeterRates": k["MeterRates"]['0'],
                                        "MeterName": k["MeterName"],
                                        "MeterCategory": k["MeterCategory"],
                                        "MeterSubCategory": k["MeterSubCategory"]})
    return dictData


# RateとQuantityデータを結合する
def JoinQuantityAndRate(usageDictData,rateDictData):
    dictData = dict()
    for k in usageDictData:
        dictData[k] = dict({"quantity": usageDictData[k]["quantity"],
                            "MeterRates": rateDictData[k]["MeterRates"],
                            "MeterName": rateDictData[k]["MeterName"],
                            "MeterFee": usageDictData[k]["quantity"] * rateDictData[k]["MeterRates"],
                            "MeterCategory": rateDictData[k]["MeterCategory"],
                            "MeterSubCategory": rateDictData[k]["MeterSubCategory"]})
    return dictData


# カテゴリごとに利用金額(円)を合計する
def SumUsagefeeByCategory(inData):
    counterData = Counter()
    for k in inData:
        counterData[inData[k]['MeterCategory']] += inData[k]['MeterFee']
    dictData = dict()
    for k,v in dict(counterData).items():
        dictData[k] = int(v)
    return dictData


# 合計利用金額(円)を取得する
def SumUsagefee(inData):
    counterData = Counter()
    for k in inData:
        counterData['ALL'] += inData[k]['MeterFee']
    return int(counterData['ALL'])


# TOKENの取得 
token = GetAuthToken()

# 利用データの取得
usageJson = GetAzureResourceUsage(subscriptionsID,token,startDayStr,lastDayStr)
usageDict = GetQuantitySum(usageJson)

# レートデータの取得
reteJson = GetAzureRateCard(subscriptionsID,token,offerDurableID)
rateDict = GetRateData(reteJson)

# データの結合・合計
usageData = JoinQuantityAndRate(usageDict,rateDict)
categorySum = SumUsagefeeByCategory(usageData)
totalSum = SumUsagefee(usageData)

# ==== SlackへPostする ====
slackText='今月の、昨日までのAzureの利用料金は￥' + "{:,d}".format(totalSum) + 'になります。\n ' + \
           '※ 日本円計算のため実際の請求(ドル計算)とは差異が発生します。'

slack=slackweb.Slack(url=slackWebhook)
attachments=[]
attachment={'pretext': '各サービス別の利用料金','fields': []}

for k in categorySum:
    item={'title': k ,'value': '￥' + "{:,d}".format(categorySum[k]) ,'short': "true"}
    attachment['fields'].append(item)

attachments.append(attachment)
slack.notify(text=slackText, channel=slackChannel, username=slackUser, icon_emoji=":azure-icon:", attachments=attachments)

