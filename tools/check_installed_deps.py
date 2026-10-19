import gymnasium as gym
import gym_donkeycar

print(gym_donkeycar.__file__)
print([k for k in gym.registry if "donkey" in k])