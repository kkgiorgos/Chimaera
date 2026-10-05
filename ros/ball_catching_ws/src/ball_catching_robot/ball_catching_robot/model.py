"""FR3 kinematics/dynamics from calibrated URDF, independent of Gazebo."""
import xml.etree.ElementTree as ET
import numpy as np
import PyKDL as kdl


def array(values):
    q = kdl.JntArray(len(values))
    for i, value in enumerate(values):
        q[i] = float(value)
    return q


def vector(element, attribute, default):
    return np.array([float(x) for x in element.get(attribute, default).split()])


def frame(origin):
    if origin is None:
        return kdl.Frame.Identity()
    return kdl.Frame(kdl.Rotation.RPY(*vector(origin, 'rpy', '0 0 0')),
                     kdl.Vector(*vector(origin, 'xyz', '0 0 0')))


class Arm:
    def __init__(self, urdf):
        root = ET.fromstring(urdf)
        links = {x.get('name'): x for x in root.findall('link')}
        parents = {x.find('child').get('link'): x for x in root.findall('joint')}
        path, name = [], 'cup_rim'
        while name != 'fr3_link0':
            joint = parents[name]
            path.append(joint)
            name = joint.find('parent').get('link')
        path_names = {joint.get('name') for joint in path}
        branches = {}
        for joint in root.findall('joint'):
            if joint.get('type') == 'fixed' and joint.get('name') not in path_names:
                branches.setdefault(joint.find('parent').get('link'), []).append(joint)

        def body(link):
            inertia = kdl.RigidBodyInertia.Zero()
            inertial = link.find('inertial')
            if inertial is not None:
                origin = frame(inertial.find('origin'))
                tensor = inertial.find('inertia')
                matrix = np.array([[float(tensor.get('ixx')), float(tensor.get('ixy')), float(tensor.get('ixz'))],
                                   [float(tensor.get('ixy')), float(tensor.get('iyy')), float(tensor.get('iyz'))],
                                   [float(tensor.get('ixz')), float(tensor.get('iyz')), float(tensor.get('izz'))]])
                rotation = np.array([[origin.M[i, j] for j in range(3)] for i in range(3)])
                matrix = rotation @ matrix @ rotation.T
                inertia = kdl.RigidBodyInertia(float(inertial.find('mass').get('value')), origin.p,
                    kdl.RotationalInertia(matrix[0, 0], matrix[1, 1], matrix[2, 2],
                                          matrix[0, 1], matrix[0, 2], matrix[1, 2]))
            # Fixed side branches (including the mounting bracket) contribute
            # inertia to their parent even though they are not on the TCP chain.
            for joint in branches.get(link.get('name'), []):
                inertia = inertia + frame(joint.find('origin')) * body(links[joint.find('child').get('link')])
            return inertia

        self.chain = kdl.Chain()
        self.names, lower, upper, velocity, effort = [], [], [], [], []
        for joint in reversed(path):
            name = joint.get('name')
            link = links[joint.find('child').get('link')]
            inertia = body(link)
            origin = frame(joint.find('origin'))
            if joint.get('type') == 'revolute':
                self.chain.addSegment(kdl.Segment(name + '_origin', kdl.Joint(kdl.Joint.Fixed), origin))
                axis = kdl.Vector(*vector(joint.find('axis'), 'xyz', '0 0 1'))
                self.chain.addSegment(kdl.Segment(link.get('name'),
                    kdl.Joint(name, kdl.Vector.Zero(), axis, kdl.Joint.RotAxis),
                    kdl.Frame.Identity(), inertia))
                self.names.append(name)
                limits = joint.find('limit')
                lower.append(float(limits.get('lower')))
                upper.append(float(limits.get('upper')))
                velocity.append(float(limits.get('velocity')))
                effort.append(float(limits.get('effort')))
            elif joint.get('type') == 'fixed':
                self.chain.addSegment(kdl.Segment(link.get('name'), kdl.Joint(kdl.Joint.Fixed), origin, inertia))
            else:
                raise ValueError('Only fixed/revolute arm joints are supported')
        self.lower, self.upper = np.array(lower), np.array(upper)
        self.velocity, self.effort = np.array(velocity), np.array(effort)
        # FR3 limits: https://frankarobotics.github.io/docs/robot_specifications.html
        self.acceleration = np.full(7, 10.)
        self.velocity_offset = np.array([.6599, .2517, .2000, .3533, .5757, .4878, .4628])
        self.deceleration = np.array([6., 2.585, 3.5, 4., 17., 5.5, 17.])
        self.fk = kdl.ChainFkSolverPos_recursive(self.chain)
        self.jacobian = kdl.ChainJntToJacSolver(self.chain)
        self.dynamics = kdl.ChainDynParam(self.chain, kdl.Vector(0, 0, -9.81))

    def pose(self, q):
        result = kdl.Frame()
        self.fk.JntToCart(array(q), result)
        return result

    def inverse(self, position, rotation, seed, iterations=35):
        target = kdl.Frame(rotation, kdl.Vector(*position))
        q = np.asarray(seed).copy()
        for _ in range(iterations):
            current = self.pose(q)
            error = kdl.diff(current, target)
            delta = np.array([error[i] for i in range(6)])
            if np.linalg.norm(delta[:3]) < 0.002 and np.linalg.norm(delta[3:]) < 0.015:
                return q
            jac = kdl.Jacobian(7)
            self.jacobian.JntToJac(array(q), jac)
            j = np.array([[jac[i, k] for k in range(7)] for i in range(6)])
            step = j.T @ np.linalg.solve(j @ j.T + 0.002 ** 2 * np.eye(6), delta)
            q = np.clip(q + step * min(1., 0.18 / max(np.max(np.abs(step)), 1e-9)),
                        self.lower + 0.025, self.upper - 0.025)
        return None

    def forces(self, q, dq):
        gravity, coriolis = kdl.JntArray(7), kdl.JntArray(7)
        self.dynamics.JntToGravity(array(q), gravity)
        self.dynamics.JntToCoriolis(array(q), array(dq), coriolis)
        return np.array([gravity[i] + coriolis[i] for i in range(7)])

    def mass(self, q):
        matrix = kdl.JntSpaceInertiaMatrix(7)
        self.dynamics.JntToMass(array(q), matrix)
        return np.array([[matrix[i, j] for j in range(7)] for i in range(7)])

    def speed_limits(self, q):
        upper = np.minimum(self.velocity, np.maximum(0., -self.velocity_offset +
                           np.sqrt(np.maximum(0., 2 * self.deceleration * (self.upper - q)))))
        lower = -np.minimum(self.velocity, np.maximum(0., -self.velocity_offset +
                            np.sqrt(np.maximum(0., 2 * self.deceleration * (q - self.lower)))))
        return lower, upper
