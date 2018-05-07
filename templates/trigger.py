from flask import Flask

app = Flask(__name__)

@app.route("/failover")
def post():
    open('/opt/pgsql/data/postgresql.trigger', 'w').close()
    return 'Success'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
