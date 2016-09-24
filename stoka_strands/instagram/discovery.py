import bs4, json
import requests
import json
import sys
import pika

requests.packages.urllib3.disable_warnings()

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

class InstagramSecretAPI:
    
    paths = {
        "query": 'https://www.instagram.com/query/'
    }
    def query(self,data, csrf="w0onO0YkXQOT5gnC6srgOGbvbZ3tDDaC"):
        cookie = "mid=V76nuQAEAAH-CuEOAdoMiatCGu5Z; fbm_124024574287414=base_domain=.instagram.com; sessionid=IGSC2e745c64acfec2c25f6b9ba66880db3560c5b6baf5cc040f437a113321a740be%3AuO4GMPJCl1i1IJHUvQXMCXh5siI4IkTx%3A%7B%22_token_ver%22%3A2%2C%22_auth_user_id%22%3A3776064946%2C%22_token%22%3A%223776064946%3AghEjJxxVFeyX135oEPUGCr5zrPOdjSYe%3A3f2e23e7befda143990030fd057bbedb047b93d234bcb542e421d951283e5c8e%22%2C%22_auth_user_backend%22%3A%22accounts.backends.CaseInsensitiveModelBackend%22%2C%22last_refreshed%22%3A1474631055.237057%2C%22_platform%22%3A4%2C%22_auth_user_hash%22%3A%22%22%7D; ig_pr=2; ig_vw=1266; csrftoken=w0onO0YkXQOT5gnC6srgOGbvbZ3tDDaC; s_network=; ds_user_id=3776064946"

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

class InstagramGraphQueryRequest:
    REF_USER_SHOW = "users::show"
    def __init__(self, ig_user, ref=REF_USER_SHOW):
        self.ig_user = ig_user
        self.ref = ref
    
    def buildQ(self):
        nodes = Nodes()
        return "ig_user(%s) %s" % (self.ig_user, [
            'count', 
            nodes,
            'page_info'
        ])

    def getFormData(self):
        return {
            "q": self.buildQ(),
            "ref": self.ref
        }

# Expose this for AlphaStoka
class StokaInstance:
    option_descriptions = (('ig_user', 'initial seed node (user id)', 184742362))
    seed_user = {"id": 184742362};

    def __init__(self, rabbit_mq_connection, options={}, group_name="default_name"):
        self.igSecret = InstagramSecretAPI()
        self.seed_user = options['ig_user'];
        self.group_name = group_name;
        self.rabbit_channel = rabbit_mq_connection.channel();
        self.rabbit_channel.queue_declare(queue=group_name,durable=True)
        #seed the queue
        self.pushQ(self.seed_user)

        
    STORAGE = {}
    Q = []
    def save(self, object):
        self.STORAGE[object["id"]] = object
    
    def inStorage(self, object):
        return object["id"] in self.STORAGE

    def pushQ(self, object):
        self.rabbit_channel.basic_publish(exchange='',
                      routing_key=self.group_name,
                      body=json.dumps(object),
                      properties=pika.BasicProperties(
                         delivery_mode = 2, # make message persistent
                      ))
        #self.Q.append(object)
    
    def _rabbit_consume_callback(self,ch, method, properties, body):
        # print(" [x] Received %r" % (body,))
        # time.sleep( body.count('.') )
        # print(" [x] Done")
        ch.basic_ack(delivery_tag = method.delivery_tag)
        p = json.loads(body.decode("utf-8") )

        print("[x] Working on ", p["id"], "(%s)" % (p["username"],))
        # p = json.loads()

        if self.inStorage(p):
            print("In storage", p["id"])
            return

        #save visited node
        self.save(p)
        # print(p)
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
            print("[x] Sent to ", self.group_name, f["id"], "(%s)" % (f["username"],))


    def popQ(self):
        self.rabbit_channel.basic_qos(prefetch_count=1)
        self.rabbit_channel.basic_consume(self._rabbit_consume_callback,
                      queue=self.group_name)
        self.rabbit_channel.start_consuming()
        

    def find_followers(self, node_id):
        root = InstagramRequestNode("ig_user(%s)" % str(node_id))
        follow = InstagramRequestNode("followed_by.first(100)")
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
    
        
    def run(self):
        #do wrok
        self.popQ()
        
        
    def run_sync(self):
        while True:
            print("In Queue", len(self.Q), "Stored", len(self.STORAGE))
            p = self.popQ()
            
            if self.inStorage(p):
                print("In storage", p["id"])
                continue

            #save visited node
            self.save(p)
            # print(p)
            F = self.find_followers(p["id"])
            if "nodes" not in F["followed_by"]:
                continue
            if "followed_by" not in F:
                continue
            for f in F["followed_by"]["nodes"]:
                self.pushQ(f)
            

if __name__ == '__main__':
    credentials = pika.PlainCredentials('rabbitmq', 'xEDUqAxIZY7nhe40')
    print("Connecting to Rabbit..")
    connection = pika.BlockingConnection(pika.ConnectionParameters(
               'localhost', port=32774, credentials=credentials))
            
    print("Starting Stoka..")
    instance = StokaInstance(connection,{
        "ig_user": {"id" : 184742362}
    }, group_name="discovery_queue")

    instance.run()
