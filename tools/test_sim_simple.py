import gymnasium as gym
import gym_donkeycar

# (donkey) root@FajarLaksono-PC:~# ip route | grep default
# default via 172.27.64.1 dev eth0 proto kernel

conf = {"exe_path": "remote", "host": "127.0.0.1", "port": 9091}
# conf = {"exe_path": "remote", "host": "172.27.64.1", "port": 9091}
env = gym.make("donkey-generated-track-v0", conf=conf)
obs, info = env.reset()

for i in range(200):
    obs, reward, terminated, truncated, info = env.step(env.action_space.sample())
    print(i, info.get("speed"), info.get("cte"))
    if terminated or truncated:
        obs, info = env.reset()
env.close()