import bs4, json
import requests
import json
import sys, os
import pika, re
from pymongo import MongoClient
from langid.langid import LanguageIdentifier, model

from categorizer import categorize

requests.packages.urllib3.disable_warnings()

#
# This is the data structure 
# for Instagram request form-data format
# which is tree-like / json-ish but 
# not quite json 
# if you know what i mean
class InstagramRequestNode:
    def __init__(self, name):
        self.name = name
        self.children = []
    
    def add(self, child):
        self.children.append(child)
        return self

    def user_template(self):
        return self.add("id").add("is_verified").add(InstagramRequestNode("followed_by").add("count")).add("biography").add("thumbnail_src").add("profile_pic_url").add("username").add(InstagramRequestNode("media.after(0, 12)").media_template())
    
    def media_template(self):
        return self.add("count").add(InstagramRequestNode("nodes").add("thumbnail_src").add("caption").add("code").add(InstagramRequestNode("likes").add("count")))

    def __str__(self):
        lst_children = []
        for c in self.children:
            lst_children.append(str(c))
        return "%s {%s}" % (self.name, ",\n".join(lst_children))

#
# Instagram secret api caller 
#
_SAMPLE_COOKIE = "mid=V76nuQAEAAH-CuEOAdoMiatCGu5Z; fbm_124024574287414=base_domain=.instagram.com; sessionid=IGSC2e745c64acfec2c25f6b9ba66880db3560c5b6baf5cc040f437a113321a740be%3AuO4GMPJCl1i1IJHUvQXMCXh5siI4IkTx%3A%7B%22_token_ver%22%3A2%2C%22_auth_user_id%22%3A3776064946%2C%22_token%22%3A%223776064946%3AghEjJxxVFeyX135oEPUGCr5zrPOdjSYe%3A3f2e23e7befda143990030fd057bbedb047b93d234bcb542e421d951283e5c8e%22%2C%22_auth_user_backend%22%3A%22accounts.backends.CaseInsensitiveModelBackend%22%2C%22last_refreshed%22%3A1474631055.237057%2C%22_platform%22%3A4%2C%22_auth_user_hash%22%3A%22%22%7D; ig_pr=2; ig_vw=1266; csrftoken=w0onO0YkXQOT5gnC6srgOGbvbZ3tDDaC; s_network=; ds_user_id=3776064946"
class InstagramSecretAPI:
    
    paths = {
        "query": 'https://www.instagram.com/query/'
    }

    def curl(self, url, cookie=_SAMPLE_COOKIE):
        # build request header
        # as realistically as possible to mimic browser
        headers = {
            "origin": "https://www.instagram.com",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "en-US,en;q=0.8,th;q=0.6,ja;q=0.4",
            "x-requested-with": "XMLHttpRequest",
            "cookie": cookie,
            "pragma": "no-cache",
            "x-instagram-ajax": "1",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.116 Safari/537.36",
            "accept": "*/*",
            "cache-control": "no-cache"
        }
        res = requests.get(url, verify=False, headers=headers )
        return res.text

    def query(self,data, cookie=_SAMPLE_COOKIE, csrf="w0onO0YkXQOT5gnC6srgOGbvbZ3tDDaC"):

        # build request header
        # as realistically as possible to mimic browser
        headers = {
            "origin": "https://www.instagram.com",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "en-US,en;q=0.8,th;q=0.6,ja;q=0.4",
            "x-requested-with": "XMLHttpRequest",
            "cookie": cookie,
            "x-csrftoken": csrf,
            "pragma": "no-cache",
            "x-instagram-ajax": "1",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.116 Safari/537.36",
            "content-type": "application/x-www-form-urlencoded",
            "accept": "*/*",
            "cache-control": "no-cache",
            "authority": "www.instagram.com",
            "referer": "https://www.instagram.com/nancyajram/"
        }
        
        res = requests.post(self.paths["query"], verify=False, data=data, headers=headers )
        return res.text

#
# This is used to build form-data request
# which i assume is some sort of graph query
class InstagramGraphQueryRequest:
    REF_USER_SHOW = "users::show"
    
    def __init__(self, ig_user, ref=REF_USER_SHOW):
        self.ig_user = ig_user
        self.ref = ref
    
    #
    # the q property of form data request
    #
    def buildQ(self):
        nodes = Nodes()
        return "ig_user(%s) %s" % (self.ig_user, [
            'count', 
            nodes,
            'page_info'
        ])
    
    #
    # Serialize as form data
    #
    def getFormData(self):
        return {
            "q": self.buildQ(),
            "ref": self.ref
        }

# this is for the actual crawling
class StokaInstance:

    def __init__(self, rabbit_mq_connection, ig_user, group_name="default_stoka"):
        self.igSecret = InstagramSecretAPI()
        self.group_name = group_name;
        self.rabbit_channel = rabbit_mq_connection.channel();
        self.rabbit_channel.queue_declare(queue=group_name,durable=True)
        self.mongo_client = MongoClient("mongodb://54.169.89.105:27017")
        self.mongo_db = self.mongo_client['stoka_' + group_name]
        self.mongo_system = self.mongo_client['stoka_system']
        self.lidentifier = LanguageIdentifier.from_modelstring(model, norm_probs=True)

        for doc in self.mongo_system.categorizer.find({}).skip(0).limit(1):
            del doc["_id"]
            self.categorizer_kwd = doc
            break

        #seed the queue
        seed_user_obj = self.get_user(ig_user)
        self.seed_user = seed_user_obj
        # print(seed_user_obj)
        self.astoka_progress = 0
        self.astoka_error = 0
        self.pushQ(seed_user_obj)

    STORAGE = {}
    Q = []

    #
    # Procesisng of the object in each iteration of pop()
    # object = User object (contains id, and username etc.)
    #
    def process(self, object):
        self.astoka_progress = self.astoka_progress + 1
        self.save(object)
        print("@astoka.progress ", self.astoka_progress)
        print("@astoka.error ", self.astoka_error)

    # persist to mongodb
    def save(self, object):
        # short term memory checking if we have seen this
        self.STORAGE[object["id"]] = True
        object["_seed_username"] = self.seed_user["username"]
        object["_dna"] = "stoka-ig"
        object["predicted_age"] = "pending"
        object["category"] = {}
        object["language"] = "-"
        LNG = self.lidentifier.classify(self.getDescription())
        if len(LNG) > 0:
            object["language"] = LNG[0]

        captions = ""
        try:
            for node in object["media"]["nodes"]:
                print(node)
                captions +=  node["caption"]
        except KeyError as ee:
            print(ee)
            self.astoka_error = self.astoka_error + 1

        confidence = categorize(str(object["biography"]) + str(captions), self.categorizer_kwd)
        object["category"] = confidence

        try:
            result = self.mongo_db.instagram.insert_one(object)
            print("[x] Persisting %s (%s) / mongoId -> %s" % (object["id"], object["username"], result.inserted_id))
        except Exception as ex:
            self.astoka_error = self.astoka_error + 1
            print("[o] Exception while saving to mongo (might be duplicate)", ex)

    
    # check if it's in mongo or in some sort of fast memoryview
    # this is for preventing dupe , it's not 100% proof but it's better than nthing
    def inStorage(self, object):
        return object["id"] in self.STORAGE

    # push object to work queue
    # so other can pick up this object and populate the queue
    # with the object's follower
    def pushQ(self, object):
        self.rabbit_channel.basic_publish(exchange='',
                      routing_key=self.group_name,
                      body=json.dumps(object),
                      properties=pika.BasicProperties(
                         delivery_mode = 2, 
                      ))
        # print("[x] Sent to ", self.group_name, object["id"], "(%s)" % (object["username"],))
        #self.Q.append(object)
    
    ## Called on pop done
    # this is async pop callback
    def _rabbit_consume_callback(self,ch, method, properties, body):
        # print(" [x] Received %r" % (body,))
        # time.sleep( body.count('.') )
        # print(" [x] Done")
        ch.basic_ack(delivery_tag = method.delivery_tag)
        p = json.loads(body.decode("utf-8") )

        # print("[x] Working on ", p["id"], "(%s)" % (p["username"],))
        # print(p)
        F = self.find_suggested(p["id"])
        
        if "chaining" not in F:
            return
        if "nodes" not in F["chaining"]:
            return
        # for f in F["followed_by"]["nodes"]:
        for f in F["chaining"]["nodes"]:
            if self.inStorage(f):
                # print("[o] Skipped ", f["id"], "(%s)" % (f["username"],))
                continue

            
            
            self.process(f)
            self.pushQ(f)

        F = self.find_followers(p["id"])
        if "nodes" not in F["followed_by"]:
            return
        if "followed_by" not in F:
            return
        for f in F["followed_by"]["nodes"]:
            if self.inStorage(f):
                # print("[o] Skipped ", f["id"], "(%s)" % (f["username"],))
                continue

            # pass down the pipeline
            
            
            self.process(f)
            self.pushQ(f)

    # popping (called once)
    def popQ(self):
        self.rabbit_channel.basic_qos(prefetch_count=1)
        self.rabbit_channel.basic_consume(self._rabbit_consume_callback,
                      queue=self.group_name)
        # this is blocking (forever)
        self.rabbit_channel.start_consuming()
    

    def get_user(self, node_id):
        if(node_id[0] == "@"):
            #user passed in username(str)
            
            raw = self.igSecret.curl("https://instagram.com/" + node_id[1:])
            m = re.search(r'window._sharedData = ([\s\S]*?);</script>', raw)
            igram = json.loads(m.group(1))
            return igram["entry_data"]["ProfilePage"][0]["user"]

        root = InstagramRequestNode("ig_user(%s)" % str(node_id))
        root.user_template()
        reqbody = {
            "q": str(root),
            "ref": ""
        }
        raw = self.igSecret.query(reqbody)
        return json.loads(raw)

    
    # find follower
    # by calling instagram secret API 
    # using the Object popped's id
    def find_followers(self, node_id, max=20):
        root = InstagramRequestNode("ig_user(%s)" % str(node_id))
        follow = InstagramRequestNode("followed_by.first(%d)" % (max,))
        follow.add("count")
        follow.add(InstagramRequestNode("page_info").add("end_cursor").add("has_next_page"))
        follow.add(InstagramRequestNode("nodes").user_template())
        root.add(follow)
        reqbody = {
            "q": str(root),
            "ref": "relationships::follow_list"
        }
        raw = self.igSecret.query(reqbody)
        # print(raw)
        return json.loads(raw)
    
    # find suggested
    def find_suggested(self, node_id):
        root = InstagramRequestNode("ig_user(%s)" % str(node_id))
        chaining = InstagramRequestNode("chaining")
        nodes = InstagramRequestNode("nodes").user_template()
        chaining.add(nodes)
        root.add(chaining)
        # root.add("biography").add("likes").add("thumbnail_src").add("follows")
        reqbody = {
            "q": str(root),
            "ref": ""
        }
        raw = self.igSecret.query(reqbody)
        # print(reqbody)
        # print(raw)
        try:
            px = json.loads(raw)
        except Exception as x:
            print("[o] Error deserializing")
            print(x)
        return px
    
    # entry point
    def run(self):
        #do work!
        self.popQ()


if __name__ == '__main__':
    RABBIT_USR = os.getenv('RABBIT_USR', "rabbitmq")
    RABBIT_PWD = os.getenv('RABBIT_PWD', "Nc77WrHuAR58yUPl")
    RABBIT_PORT = os.getenv('RABBIT_PORT', 32774)
    RABBIT_HOST = os.getenv('RABBIT_HOST', 'localhost')
    SEED_ID = os.getenv('SEED_ID', '1281620107')
    GROUP_NAME = os.getenv('GROUP_NAME', 'discovery_queue_4')

    print("using configuration", RABBIT_HOST, RABBIT_PWD, RABBIT_USR, int(RABBIT_PORT))

    # credentials = pika.PlainCredentials('rabbitmq', 'xEDUqAxIZY7nhe40')
    credentials = pika.PlainCredentials(RABBIT_USR, RABBIT_PWD)
    print("Connecting to Rabbit..")
    connection = pika.BlockingConnection(pika.ConnectionParameters(
               RABBIT_HOST, port=int(RABBIT_PORT), credentials=credentials))
            
    print("Starting Stoka..")
    
        
    instance = StokaInstance(connection,ig_user=SEED_ID, group_name=GROUP_NAME)

    instance.run()
