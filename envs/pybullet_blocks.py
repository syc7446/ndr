"""A blocks environment written in Pybullet.

Based on the environment described in ZPK.
"""
from ndr.structs import Predicate, LiteralConjunction, Type, Anti
from .spaces import LiteralSpace, LiteralSetSpace

from gym import utils, spaces
from gym.utils import seeding
import gym
import numpy as np
import itertools

import pybullet as p

import glob
import os
import pdb


DIR_PATH = os.path.dirname(os.path.abspath(__file__))
DEBUG = True

### First set up an environment that is just the low-level physics
def inverse_kinematics(body_id, end_effector_id, target_position, target_orientation, joint_indices, physics_client_id=-1):
    """
    Parameters
    ----------
    body_id : int
    end_effector_id : int
    target_position : (float, float, float)
    target_orientation : (float, float, float, float)
    joint_indices : [ int ]
    
    Returns
    -------
    joint_poses : [ float ] * len(joint_indices)
    """
    lls, uls, jrs, rps = get_joint_ranges(body_id, joint_indices, physics_client_id=physics_client_id)

    all_joint_poses = p.calculateInverseKinematics(body_id, end_effector_id, target_position,
        targetOrientation=target_orientation,
        lowerLimits=lls, upperLimits=uls, jointRanges=jrs, restPoses=rps,
        physicsClientId=physics_client_id)

    # Find the free joints
    free_joint_indices = []

    num_joints = p.getNumJoints(body_id, physicsClientId=physics_client_id)
    for idx in range(num_joints):
        joint_info = p.getJointInfo(body_id, idx, physicsClientId=physics_client_id)
        if joint_info[3] > -1:
            free_joint_indices.append(idx)

    # Find the poses for the joints that we want to move
    joint_poses = []

    for idx in joint_indices:
        free_joint_idx = free_joint_indices.index(idx)
        joint_pose = all_joint_poses[free_joint_idx]
        joint_poses.append(joint_pose)

    return joint_poses

def get_joint_ranges(body_id, joint_indices, physics_client_id=-1):
    """
    Parameters
    ----------
    body_id : int
    joint_indices : [ int ]

    Returns
    -------
    lower_limits : [ float ] * len(joint_indices)
    upper_limits : [ float ] * len(joint_indices)
    joint_ranges : [ float ] * len(joint_indices)
    rest_poses : [ float ] * len(joint_indices)
    """
    lower_limits, upper_limits, joint_ranges, rest_poses = [], [], [], []

    num_joints = p.getNumJoints(body_id, physicsClientId=physics_client_id)

    for i in range(num_joints):
        joint_info = p.getJointInfo(body_id, i, physicsClientId=physics_client_id)

        # Fixed joint so ignore
        qIndex = joint_info[3]
        if qIndex <= -1:
            continue

        ll, ul = -2., 2.
        jr = 2.

        # For simplicity, assume resting state == initial state
        rp = p.getJointState(body_id, i, physicsClientId=physics_client_id)[0]

        # Fix joints that we don't want to move
        if i not in joint_indices:
            ll, ul =  rp-1e-8, rp+1e-8
            jr = 1e-8

        lower_limits.append(ll)
        upper_limits.append(ul)
        joint_ranges.append(jr)
        rest_poses.append(rp)

    return lower_limits, upper_limits, joint_ranges, rest_poses

def get_kinematic_chain(robot_id, end_effector_id, physics_client_id=-1):
    """
    Get all of the free joints from robot base to end effector.

    Includes the end effector.

    Parameters
    ----------
    robot_id : int
    end_effector_id : int
    physics_client_id : int

    Returns
    -------
    kinematic_chain : [ int ]
        Joint ids.
    """
    kinematic_chain = []
    while end_effector_id > 0:
        joint_info = p.getJointInfo(robot_id, end_effector_id, physicsClientId=physics_client_id)
        if joint_info[3] > -1:
            kinematic_chain.append(end_effector_id)
        end_effector_id = joint_info[-1]
    return kinematic_chain


class LowLevelPybulletBlocksEnv(gym.Env):

    metadata = {'render.modes': ['human', 'rgb_array'], 'video.frames_per_second': 10}

    def __init__(self, use_gui=True, sim_steps_per_action=20, physics_client_id=None, max_joint_velocity=0.1):

        self.sim_steps_per_action = sim_steps_per_action
        self.max_joint_velocity = max_joint_velocity

        self.distance_threshold = 0.05

        self.base_position = [0.405 + 0.2869, 0.48 + 0.2641, 0.0]
        self.base_orientation = [0., 0., 0., 1.]

        self.table_height = 0.42 + 0.205

        self.camera_distance = 1.5
        self.yaw = 90
        self.pitch = -24
        self.camera_target = [1.65, 0.75, 0.42]

        if physics_client_id is None:
            if use_gui:
                self.physics_client_id = p.connect(p.GUI)
                p.resetDebugVisualizerCamera(self.camera_distance, self.yaw, self.pitch, self.camera_target)
            else:
                self.physics_client_id = p.connect(p.DIRECT)
        else:
            self.physics_client_id = physics_client_id

        self.use_gui = use_gui
        self.setup()
        self.seed()

    def sample_initial_state(self):
        """Sample blocks
        """
        # Block name : block state
        state = {}
        block_name_counter = itertools.count()

        # For now, constant orientation (quaternion) for all blocks.
        orn_x, orn_y, orn_z, orn_w = 0., 0., 0., 1.

        num_piles = self.np_random.randint(1, 4)
        for pile in range(num_piles):
            num_blocks_in_pile = 1 #self.np_random.randint(1, 4)
            # Block stack blocks.
            x, y = 1.25, 0.5 + pile*0.2
            previous_block_top = 0.5
            for i in range(num_blocks_in_pile):
                block_name = "block{}".format(next(block_name_counter))
                w, l, h, mass, friction = self.sample_block_static_attributes()
                z = previous_block_top + h/2.
                previous_block_top += h
                attributes = [w, l, h, x, y, z, orn_x, orn_y, orn_z, orn_w, mass, friction]
                state[block_name] = attributes

        return state

    def sample_block_static_attributes(self):
        w, l, h = self.np_random.normal(0.075, 0.005, size=(3,))
        mass = self.np_random.uniform(0.05, 0.2)
        friction = 1.
        return w, l, h, mass, friction

    def setup(self):
        p.resetSimulation(physicsClientId=self.physics_client_id)

        # Load plane
        p.setAdditionalSearchPath(DIR_PATH)
        p.loadURDF("assets/urdf/plane.urdf", [0, 0, -1], useFixedBase=True, physicsClientId=self.physics_client_id)

        # Load Fetch
        self.fetch_id = p.loadURDF("assets/urdf/robots/fetch.urdf", useFixedBase=True, physicsClientId=self.physics_client_id)
        p.resetBasePositionAndOrientation(self.fetch_id, self.base_position, self.base_orientation, physicsClientId=self.physics_client_id)

        # Get end effector
        joint_names = [p.getJointInfo(self.fetch_id, i, physicsClientId=self.physics_client_id)[1].decode("utf-8") \
                       for i in range(p.getNumJoints(self.fetch_id, physicsClientId=self.physics_client_id))]

        self.ee_id = joint_names.index('gripper_axis')
        self.ee_orientation = [1., 0., -1., 0.]

        self.arm_joints = get_kinematic_chain(self.fetch_id, self.ee_id, physics_client_id=self.physics_client_id)
        self.left_finger_id = joint_names.index("l_gripper_finger_joint")
        self.right_finger_id = joint_names.index("r_gripper_finger_joint")
        self.arm_joints.append(self.left_finger_id)
        self.arm_joints.append(self.right_finger_id)

        # Load table
        table_urdf = "assets/urdf/table.urdf"
        table_id = p.loadURDF(table_urdf, useFixedBase=True, physicsClientId=self.physics_client_id)
        p.resetBasePositionAndOrientation(table_id, (1.65, 0.75, 0.0), [0., 0., 0., 1.], physicsClientId=self.physics_client_id)

        # Blocks are created at reset
        self.block_ids = {}

        # Set gravity
        p.setGravity(0., 0., -10., physicsClientId=self.physics_client_id)

        # Let the world run for a bit
        for _ in range(100):
            p.stepSimulation(physicsClientId=self.physics_client_id)

        # Move the arm to a good start location
        joint_values = inverse_kinematics(self.fetch_id, self.ee_id, [1., 0.5, 0.5], self.ee_orientation, self.arm_joints, 
            physics_client_id=self.physics_client_id)

        # Set arm joint motors
        for joint_idx, joint_val in zip(self.arm_joints, joint_values):
            p.resetJointState(self.fetch_id, joint_idx, joint_val, physicsClientId=self.physics_client_id)

        for _ in range(100):
            p.stepSimulation(physicsClientId=self.physics_client_id)

        # Record the initial state so we can reset to it later
        self.initial_state_id = p.saveState(physicsClientId=self.physics_client_id)

        self.action_space = spaces.Box(low=-1, high=1, shape=(4,), dtype=np.float32)

    def set_state(self, state):
        # Blocks are always recreated on reset because their size, mass, friction changes
        self.static_block_attributes = {}
        self.block_ids = {}

        for block_name, block_state in state.items():
            color = self.get_color_from_block_name(block_name)
            block_id = self.create_block(block_state, color=color)
            self.block_ids[block_name] = block_id

        # Let the world run for a bit
        for _ in range(250):
            p.stepSimulation(physicsClientId=self.physics_client_id)

    def get_color_from_block_name(self, block_name):
        colors = [
            (0.95, 0.05, 0.1, 1.),
            (0.05, 0.95, 0.1, 1.),
            (0.1, 0.05, 0.95, 1.),
            (0.4, 0.05, 0.6, 1.),
            (0.6, 0.4, 0.05, 1.),
            (0.05, 0.04, 0.6, 1.),
            (0.95, 0.95, 0.1, 1.),
            (0.95, 0.05, 0.95, 1.),
            (0.05, 0.95, 0.95, 1.),
        ]
        block_num = int(block_name[len("block"):])
        return colors[block_num % len(colors)]

    def create_block(self, attributes, color=(0., 0., 1., 1.)):
        w, l, h, x, y, z, orn_x, orn_y, orn_z, orn_w, mass, friction = attributes

        # Create the collision shape
        half_extents = [w/2., l/2., h/2.]
        collision_id = p.createCollisionShape(p.GEOM_BOX, halfExtents=half_extents, physicsClientId=self.physics_client_id)

        # Create the visual_shape
        visual_id = p.createVisualShape(p.GEOM_BOX, halfExtents=half_extents, rgbaColor=color, 
            physicsClientId=self.physics_client_id)

        # Create the body
        block_id = p.createMultiBody(baseMass=mass, baseCollisionShapeIndex=collision_id, 
            baseVisualShapeIndex=visual_id, basePosition=[x, y, z], baseOrientation=[orn_x, orn_y, orn_z, orn_w],
            physicsClientId=self.physics_client_id)
        p.changeDynamics(block_id, -1, lateralFriction=friction, physicsClientId=self.physics_client_id)

        # Cache block static attributes
        self.static_block_attributes[block_id] = (w, l, h, mass, friction)

        return block_id

    def seed(self, seed=None):
        self.np_random, seed = seeding.np_random(seed)
        self.action_space.seed(seed)
        return [seed]

    def reset(self):
        for block_id in self.block_ids.values():
            p.removeBody(block_id, physicsClientId=self.physics_client_id)

        p.restoreState(stateId=self.initial_state_id, physicsClientId=self.physics_client_id)
        p.resetBasePositionAndOrientation(self.fetch_id, self.base_position, self.base_orientation, physicsClientId=self.physics_client_id)

        initial_state = self.sample_initial_state()
        self.set_state(initial_state)

        return self.get_state(), {}

    def step(self, action):
        action *= 0.05

        ee_delta, finger_action = action[:3], action[3]

        current_position, current_orientation = p.getLinkState(self.fetch_id, self.ee_id, physicsClientId=self.physics_client_id)[4:6]
        target_position = np.add(current_position, ee_delta)

        joint_values = inverse_kinematics(self.fetch_id, self.ee_id, target_position, self.ee_orientation, self.arm_joints, 
            physics_client_id=self.physics_client_id)

        # Set arm joint motors
        for joint_idx, joint_val in zip(self.arm_joints, joint_values):
            p.setJointMotorControl2(bodyIndex=self.fetch_id, jointIndex=joint_idx, controlMode=p.POSITION_CONTROL,
                targetPosition=joint_val, physicsClientId=self.physics_client_id)

        # Set finger joint motors
        for finger_id in [self.left_finger_id, self.right_finger_id]:
            current_val = p.getJointState(self.fetch_id, finger_id, physicsClientId=self.physics_client_id)[0]
            target_val = current_val + finger_action
            p.setJointMotorControl2(bodyIndex=self.fetch_id, jointIndex=finger_id, controlMode=p.POSITION_CONTROL,
                targetPosition=target_val, physicsClientId=self.physics_client_id)

        for _ in range(self.sim_steps_per_action):
            p.stepSimulation(physicsClientId=self.physics_client_id)

        obs = self.get_state()
        done = False
        info = {}
        reward = 0.

        return obs, reward, done, info

    def close(self):
        p.disconnect(self.physics_client_id)

    def get_state(self):
        gripper_position, gripper_velocity  = p.getLinkState(self.fetch_id, self.ee_id, physicsClientId=self.physics_client_id)[4:6]
        left_finger_pos = p.getJointState(self.fetch_id, self.left_finger_id, physicsClientId=self.physics_client_id)[0]

        obs = {
            'gripper' : [gripper_position, gripper_velocity, left_finger_pos],
            'blocks' : {},
        }

        for block_name, block_id in self.block_ids.items():
            obs['blocks'][block_name] = self.get_block_attributes(block_id)

        return obs

    def get_block_attributes(self, block_id):
        w, l, h, mass, friction = self.static_block_attributes[block_id]

        (x, y, z), (orn_x, orn_y, orn_z, orn_w) = p.getBasePositionAndOrientation(block_id, 
            physicsClientId=self.physics_client_id)

        attributes = [w, l, h, x, y, z, orn_x, orn_y, orn_z, orn_w, mass, friction]
        return attributes

    def render(self, mode='human', close=False):

        if not self.use_gui:
            raise Exception("Rendering only works with GUI on. See https://github.com/bulletphysics/bullet3/issues/1157")

        view_matrix = p.computeViewMatrixFromYawPitchRoll(
            cameraTargetPosition=self.camera_target,
            distance=self.camera_distance,
            yaw=self.yaw,
            pitch=self.pitch,
            roll=0,
            upAxisIndex=2,
            physicsClientId=self.physics_client_id)

        width, height = 2*(3350//8), 2*(1800//8)

        proj_matrix = p.computeProjectionMatrixFOV(
            fov=60, aspect=float(width / height),
            nearVal=0.1, farVal=100.0,
            physicsClientId=self.physics_client_id)

        (_, _, px, _, _) = p.getCameraImage(
            width=width, height=height, viewMatrix=view_matrix,
            projectionMatrix=proj_matrix,
            renderer=p.ER_BULLET_HARDWARE_OPENGL,
            physicsClientId=self.physics_client_id)

        rgb_array = np.array(px)
        rgb_array = rgb_array[:, :, :3]
        return rgb_array



# Object types
block_type = Type("block")

# Actions
pickup = Predicate("pickup", 1, [block_type])
puton = Predicate("puton", 1, [block_type])
putontable = Predicate("putontable", 0, [])

# Controllers
atol = 1e-3
def get_move_action(gripper_position, target_position, atol=1e-3, gain=10., close_gripper=False):
    """
    Move an end effector to a position and orientation.
    """
    # Get the currents
    action = gain * np.subtract(target_position, gripper_position)
    if close_gripper:
        gripper_action = -0.1
    else:
        gripper_action = 0.
    action = np.hstack((action, gripper_action))

    return action

def block_is_grasped(left_finger_pos, gripper_position, block_position, relative_grasp_position, atol=1e-3):
    block_inside = block_inside_grippers(gripper_position, block_position, relative_grasp_position, atol=atol)
    grippers_closed = grippers_are_closed(left_finger_pos, atol=atol)
    if DEBUG: print("grippers_closed?", grippers_closed)
    return block_inside and grippers_closed

def block_inside_grippers(gripper_position, block_position, relative_grasp_position, atol=1e-3):
    relative_position = np.subtract(gripper_position, block_position)
    return np.sum(np.subtract(relative_position, relative_grasp_position)**2) < atol

def grippers_are_closed(left_finger_pos, atol=1e-3):
    return abs(left_finger_pos) - 0.035 <= atol

def grippers_are_open(left_finger_pos, atol=1e-3):
    return abs(left_finger_pos - 0.05) <= atol

def pickup_controller(objects, obs, atol=atol):
    assert len(objects) == 1
    block_name = objects[0].name

    gripper_position, _, left_finger_pos = obs['gripper']
    block_position = obs['blocks'][block_name][3:6]

    pick_height = 0.5
    relative_grasp_position = np.array([0., 0., 0.])
    target_position = block_position.copy()
    target_position[2] = pick_height
    workspace_height = 0.1

    # import ipdb; ipdb.set_trace()

    # Done
    if block_position[2] >= pick_height:
        if DEBUG: print("Done")
        return np.array([0., 0., 0., -0.01]), True

    # Bring up to pick position
    if block_is_grasped(left_finger_pos, gripper_position, block_position, 
        relative_grasp_position=relative_grasp_position, atol=atol):
        if DEBUG: print("Bring up to pick position")
        return np.array([0., 0., np.sign(pick_height-block_position[2]), -0.01]), False

    # If the block is ready to be grasped
    if block_inside_grippers(gripper_position, block_position, relative_grasp_position, atol=atol):
        # Close the grippers
        if DEBUG: print("Close the grippers")
        return np.array([0., 0., 0., -1.]), False

    # If the gripper is above the block
    target_position = np.add(block_position, relative_grasp_position)    
    if (gripper_position[0] - target_position[0])**2 + (gripper_position[1] - target_position[1])**2 < atol:

        # If the grippers are closed, open them
        if not grippers_are_open(left_finger_pos, atol=atol):
            if DEBUG: print("The grippers are closed, open them")
            return np.array([0., 0., 0., 1.]), False

        # Move down to grasp
        if DEBUG: print("Move down to grasp")
        return get_move_action(gripper_position, target_position, atol=atol), False

    # Else move the gripper to above the block
    target_position[2] += workspace_height
    if DEBUG: print("Move the gripper to above the block")
    return get_move_action(gripper_position, target_position, atol=atol), False

def puton_controller(objects, state):
    return np.zeros(4), True

def putontable_controller(objects, state):
    return np.zeros(4), True

controllers = {
    pickup : pickup_controller,
    puton : puton_controller,
    putontable : putontable_controller,
}

# State predicates
on = Predicate("on", 2, [block_type, block_type])
ontable = Predicate("ontable", 1, [block_type])
holding = Predicate("holding", 1, [block_type])
clear = Predicate("clear", 1, [block_type])
handempty = Predicate("handempty", 0, [])
observation_predicates = [on, ontable, holding, clear, handempty]

def get_observation(state):
    obs = set()
    for block_name in state["blocks"]:
        obs.add(ontable(block_name))
    return obs

# TODO move this somewhere else, it is general
def create_abstract_pybullet_env(low_level_cls, controllers, get_observation, obs_preds,
                                 controller_max_steps=100):

    class AbstractPybulletEnv(gym.Env):
        low_level_env = low_level_cls()
        action_predicates = list(controllers.keys())
        observation_predicates = obs_preds

        def __init__(self):
            self.action_space = LiteralSpace(self.action_predicates)
            self.observation_space = LiteralSetSpace(set(self.observation_predicates))

        def reset(self):
            low_level_obs, debug_info = self.low_level_env.reset()
            obs = get_observation(low_level_obs)
            self._previous_low_level_obs = low_level_obs
            self._problem_objects = sorted({ v for lit in obs for v in lit.variables })
            self.action_space.update(self._problem_objects)
            return obs, debug_info

        def step(self, action):
            controller = controllers[action.predicate]
            low_level_obs = self._previous_low_level_obs
            reward = 0.
            done = False
            for _ in range(controller_max_steps):
                control, controller_done = controller(action.variables, low_level_obs)
                if controller_done:
                    break
                low_level_obs, low_level_reward, done, debug_info = self.low_level_env.step(control)
                reward += low_level_reward
                if done:
                    break
            obs = get_observation(low_level_obs)
            self._previous_low_level_obs = low_level_obs 
            return obs, reward, done, {}

        def render(self, *args, **kwargs):
            return self.low_level_env.render(*args, **kwargs)

    return AbstractPybulletEnv

PybulletBlocksEnv = create_abstract_pybullet_env(LowLevelPybulletBlocksEnv, controllers, 
    get_observation, observation_predicates)

