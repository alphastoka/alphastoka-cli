import bs4, json
import requests
import json
import sys
import pika
from pymongo import MongoClient

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
    
    def __str__(self):
        lst_children = []
        for c in self.children:
            lst_children.append(str(c))
        return "%s {%s}" % (self.name, ",\n".join(lst_children))

#
# Instagram secret api caller 
#
class InstagramSecretAPI:
    
    paths = {
        "query": 'https://www.instagram.com/query/'
    }
    def query(self,data, cookie=None, csrf="w0onO0YkXQOT5gnC6srgOGbvbZ3tDDaC"):

        # cookie monster
        cookie = "mid=V76nuQAEAAH-CuEOAdoMiatCGu5Z; fbm_124024574287414=base_domain=.instagram.com; sessionid=IGSC2e745c64acfec2c25f6b9ba66880db3560c5b6baf5cc040f437a113321a740be%3AuO4GMPJCl1i1IJHUvQXMCXh5siI4IkTx%3A%7B%22_token_ver%22%3A2%2C%22_auth_user_id%22%3A3776064946%2C%22_token%22%3A%223776064946%3AghEjJxxVFeyX135oEPUGCr5zrPOdjSYe%3A3f2e23e7befda143990030fd057bbedb047b93d234bcb542e421d951283e5c8e%22%2C%22_auth_user_backend%22%3A%22accounts.backends.CaseInsensitiveModelBackend%22%2C%22last_refreshed%22%3A1474631055.237057%2C%22_platform%22%3A4%2C%22_auth_user_hash%22%3A%22%22%7D; ig_pr=2; ig_vw=1266; csrftoken=w0onO0YkXQOT5gnC6srgOGbvbZ3tDDaC; s_network=; ds_user_id=3776064946"

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
    #default seed user
    seed_user = {"id": 184742362};

    def __init__(self, rabbit_mq_connection, options={}, group_name="default_stoka"):
        self.igSecret = InstagramSecretAPI()
        self.seed_user = options['ig_user'];
        self.group_name = group_name;
        self.rabbit_channel = rabbit_mq_connection.channel();
        self.rabbit_channel.queue_declare(queue=group_name,durable=True)
        self.mongo_client = MongoClient("mongodb://localhost:27017")
        self.mongo_db = self.mongo_client['stoka_' + group_name]
        #seed the queue
        self.pushQ(self.seed_user)

        
    STORAGE = {}
    Q = []

    #
    # Procesisng of the object in each iteration of pop()
    # object = User object (contains id, and username etc.)
    #
    def process(self, object):
        self.save(object)

    # persist to mongodb
    def save(self, object):
        # print(object)
        
        # short term memory checking if we have seen this
        self.STORAGE[object["id"]] = True
        try:
            result = self.mongo_db.human.insert_one(object)
            print("[x] Persisting %s (%s) / mongoId -> %s" % (object["id"], object["username"], result.inserted_id))
        except Exception as ex:
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
        print("[x] Sent to ", self.group_name, object["id"], "(%s)" % (object["username"],))
        #self.Q.append(object)
    
    ## Called on pop done
    # this is async pop callback
    def _rabbit_consume_callback(self,ch, method, properties, body):
        # print(" [x] Received %r" % (body,))
        # time.sleep( body.count('.') )
        # print(" [x] Done")
        ch.basic_ack(delivery_tag = method.delivery_tag)
        p = json.loads(body.decode("utf-8") )

        # pass down the pipeline
        self.process(p)

        print("[x] Working on ", p["id"], "(%s)" % (p["username"],))
        
        # print(p)
        
        F = self.find_suggested(p["id"])
        
        if "chaining" not in F:
            return
        if "nodes" not in F["chaining"]:
            return
        # for f in F["followed_by"]["nodes"]:
        for f in F["chaining"]["nodes"]:
            if self.inStorage(f):
                print("[o] Skipped ", f["id"], "(%s)" % (p["username"],))
                continue

            self.pushQ(f)

        F = self.find_followers(p["id"])
        if "nodes" not in F["followed_by"]:
            return
        if "followed_by" not in F:
            return
        for f in F["followed_by"]["nodes"]:
            if self.inStorage(f):
                print("[o] Skipped ", f["id"], "(%s)" % (p["username"],))
                continue

            self.pushQ(f)

    # popping (called once)
    def popQ(self):
        self.rabbit_channel.basic_qos(prefetch_count=1)
        self.rabbit_channel.basic_consume(self._rabbit_consume_callback,
                      queue=self.group_name)
        # this is blocking (forever)
        self.rabbit_channel.start_consuming()
    

    # find posts by this node_id
    def find_posts(self, node_id, max=20):
        pass
    # find follower
    # by calling instagram secret API 
    # using the Object popped's id
    def find_followers(self, node_id, max=5):
        root = InstagramRequestNode("ig_user(%s)" % str(node_id))
        follow = InstagramRequestNode("followed_by.first(%d)" % (max,))
        follow.add("count")
        follow.add(InstagramRequestNode("page_info").add("end_cursor").add("has_next_page"))
        follow.add(InstagramRequestNode("nodes").add("id").add("is_verified").add("followed_by_viewer").add("requested_by_viewer").add("full_name").add("profile_pic_url").add("username"))
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
        nodes = InstagramRequestNode("nodes")
        nodes.add("blocked_by_viewer")
        nodes.add("followed_by_viewer")
        nodes.add("followed_by_viewer")
        nodes.add("follows_viewer")
        nodes.add("full_name")
        nodes.add("has_blocked_viewer")
        nodes.add("has_requested_viewer")
        nodes.add("id")
        nodes.add("is_private")
        nodes.add("is_verified")
        nodes.add("profile_pic_url")
        nodes.add("requested_by_viewer")
        nodes.add("username")
        chaining.add(nodes)
        root.add(chaining)
        root.add("full_name")
        reqbody = {
            "q": str(root),
            "ref": ""
        }
        raw = self.igSecret.query(reqbody)
        # print(reqbody)
        print(raw)
        return json.loads(raw)
    
    # entry point
    def run(self):
        #do work!
        self.popQ()


if __name__ == '__main__':
    credentials = pika.PlainCredentials('rabbitmq', 'xEDUqAxIZY7nhe40')
    print("Connecting to Rabbit..")
    connection = pika.BlockingConnection(pika.ConnectionParameters(
               'localhost', port=32774, credentials=credentials))
            
    print("Starting Stoka..")
    instance = StokaInstance(connection,{
        "ig_user": {"id" : 184742362, "username": "seed"}
    }, group_name="discovery_queue")

    instance.run()
