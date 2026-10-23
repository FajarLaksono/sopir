import argparse
import sys

import requests
from confluent_kafka import Producer

from sdk.driver import SeasonDriver

def main() -> None: 
    parser = argparse.ArgumentParser(description="Drive the sim and stream telementry")
    parser.add_argument("--username", required=True, help="API username")
    parser.add_argument("--password", required=True, help="API password")
    parser.add_argument("--track", default="donkey-generated-track-v0", help="gym env id")
    parser.add_argument("--driver", default="random", choices=["random", "rl"])
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--kafka", default="localhost:19092", help="broker bootstrap address")
    parser.add_argument("--sim-host", default="127.0.0.1", help="simulator host")
    parser.add_argument("--sim-port", type=int, default=9091, help="simulator port")
    parser.add_argument(
        "--model",
        default="data/models/lka_ppo_final.zip",
        help="PPO model used by --driver rl",
    )


    args = parser.parse_args()

    # 1) login
    r = requests.post(
        f"{args.api}/auth/login",
        json={"username": args.username, "password": args.password},
        timeout=10,
    )
    if r.status_code != 200:
         sys.exit(f"login failed ({r.status_code}): {r.text}")
    
    headers = {"authorization": f"Bearer {r.json()['access_token']}"}
    print(f"Logged in as {args.username}")

    # 2) Create the season
    r = requests.post(
        f"{args.api}/seasons",
        headers=headers,
        json={
            "os": "windows",
            "sdk_version": "0.1",
            "simulator_version": "1.0",
            "track_id": args.track,
            "driver": args.driver,
        },
        timeout=10,
    )
    if r.status_code != 201:
        sys.exit(f"could not create season ({r.status_code}): {r.text}")
    season_id = r.json()["id"]
    print(f"season created: {season_id}")

    # 3) drive until Ctrl+C
    producer = Producer({
        "bootstrap.servers": args.kafka,
        "client.id": f"sopir-producer-{season_id[:8]}",
    })
    season_driver = SeasonDriver(
        season_id=season_id,
        driver=args.driver,
        track_id=args.track,
        sim_host=args.sim_host,
        sim_port=args.sim_port,
        producer=producer,
        model_path=args.model,
    )
    season_driver.run()
    

if __name__ == "__main__":
    main()