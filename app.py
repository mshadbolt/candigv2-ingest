from connexion import FlaskApp
import katsu_ingest
import htsget_s3_ingest

def create_app():
    connexionApp = FlaskApp(__name__)
    app = connexionApp.app
    app.register_blueprint(katsu_ingest.ingest_blueprint)
    app.register_blueprint(htsget_s3_ingest.ingest_blueprint)
    return app

if __name__ == '__main__':
    app = create_app()
    app.run(port=1236)