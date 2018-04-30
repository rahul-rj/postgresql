from flask import Flask

app = Flask(__name__)

@app.route("/failover")
def post():
    open('/opt/pgsql/data/postgresql.trigger', 'w').close()
    return 'Success'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port={% if 'MASTER' in SERVICE_NAME|upper() %}10010{% else %}10011{% endif %})
