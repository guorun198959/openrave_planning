#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2010 Rosen Diankov (rosen.diankov@gmail.com)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import with_statement # for python 2.5
__author__ = 'Rosen Diankov'
__copyright__ = 'Copyright (C) 2010 Rosen Diankov (rosen.diankov@gmail.com)'
__license__ = 'Apache License, Version 2.0'
import roslib; roslib.load_manifest('orrosplanning')
import rospy

from optparse import OptionParser
from openravepy import *
from numpy import *
import numpy,time,threading
from itertools import izip
import tf

import orrosplanning.srv
import sensor_msgs.msg
import trajectory_msgs.msg
import geometry_msgs.msg
from IPython.Shell import IPShellEmbed

if __name__ == "__main__":
    parser = OptionParser(description='openrave planning example')
    OpenRAVEGlobalArguments.addOptions(parser)
    parser.add_option('--scene',action="store",type='string',dest='scene',default='robots/pr2-beta-static.robot.xml',
                      help='scene to load (default=%default)')
    parser.add_option('--collision_map',action="store",type='string',dest='collision_map',default='/collision_map/collision_map',
                      help='The collision map topic (maping_msgs/CollisionMap), by (default=%default)')
    parser.add_option('--ipython', '-i',action="store_true",dest='ipython',default=False,
                      help='if true will drop into the ipython interpreter rather than spin')
    (options, args) = parser.parse_args()
    env = OpenRAVEGlobalArguments.parseAndCreate(options,defaultviewer=True)

    try:
        rospy.init_node('armplanning_openrave',disable_signals=False)
        with env:
            env.Load(options.scene)
            robot = env.GetRobots()[0]
            # create ground right under the robot
            ab=robot.ComputeAABB()
            ground=RaveCreateKinBody(env,'')
            ground.SetName('map')
            ground.InitFromBoxes(array([r_[ab.pos()-array([0,0,ab.extents()[2]+0.002]),2.0,2.0,0.001]]),True)
            env.AddKinBody(ground,False)
            baseframe = robot.GetLinks()[0].GetName()
            collisionmap = RaveCreateSensorSystem(env,'CollisionMap bodyoffset %s topic %s'%(robot.GetName(),options.collision_map))
            basemanip = interfaces.BaseManipulation(robot)
        
        listener = tf.TransformListener()
        values = robot.GetDOFValues()
        valueslock = threading.Lock()
        def UpdateRobotJoints(msg):
            with valueslock:
                for name,pos in izip(msg.name,msg.position):
                    j = robot.GetJoint(name)
                    if j is not None:
                        values[j.GetDOFIndex()] = pos

        def MoveToHandPositionFn(req):
            with env:
                (robot_trans,robot_rot) = listener.lookupTransform(baseframe, robot.GetLinks()[0].GetName(), rospy.Time(0))
                Trobot = matrixFromQuat([robot_rot[3],robot_rot[0],robot_rot[1],robot_rot[2]])
                Trobot[0:3,3] = robot_trans
                hand = listener.transformPose(baseframe, req.hand_goal)
                o = hand.pose.orientation
                p = hand.pose.position
                Thandgoal = matrixFromQuat([o.w,o.x,o.y,o.z])
                Thandgoal[0:3,3] = [p.x,p.y,p.z]
                with valueslock:
                    robot.SetTransformWithDOFValues(Trobot,values)
                    
                if len(req.manip_name) > 0:
                    manip = robot.GetManipulator(req.manip_name)
                    if manip is None:
                        rospy.logerror('failed to find manipulator %s'%req.manip_name)
                        return None
                else:
                    manips = [manip for manip in robot.GetManipulators() if manip.GetEndEffector().GetName()==req.hand_frame_id]
                    if len(manips) == 0:
                        rospy.logerror('failed to find manipulator end effector %s'%req.hand_frame_id)
                        return None
                    manip = manips[0]

                handlink = robot.GetLink(req.hand_frame_id)
                if handlink is None:
                    rospy.logerror('failed to find link %s'%req.hand_frame_id)
                    return None
                if manip.GetIkSolver() is None:
                    rospy.loginfo('generating ik for %s'%str(manip))
                    ikmodel = databases.inversekinematics.InverseKinematicsModel(robot,iktype=IkParameterization.Type.Transform6D)
                    if not ikmodel.load():
                        ikmodel.autogenerate()
                
                Tgoalee = dot(Thandgoal,dot(linalg.inv(manip.GetEndEffectorTransform()),handlink.GetTransform()))
                trajdata = basemanip.MoveToHandPosition(matrices=[Tgoalee],maxtries=3,seedik=4,execute=False,outputtraj=True)
                # parse trajectory data into the ROS structure
                res = orrosplanning.srv.MoveToHandPositionResponse()
                tokens = trajdata.split()
                numpoints = int(tokens[0])
                dof = int(tokens[1])
                options = int(tokens[2])
                numvalues = dof
                offset = 0
                if options & 4:
                    numvalues += 1
                    offset += 1
                if options & 8:
                    numvalues += 7
                if options & 16:
                    numvalues += dof
                if options & 32:
                    numvalues += dof
                res.traj.joint_names = [j.GetName() for j in robot.GetJoints(manip.GetArmIndices())]
                for i in range(numpoints):
                    start = 3+numvalues*i
                    pt=trajectory_msgs.msg.JointTrajectoryPoint()
                    for j in robot.GetJoints(manip.GetArmIndices()):
                        pt.positions.append(float(tokens[start+offset+j.GetDOFIndex()]))
                    if options & 4:
                        pt.time_from_start = rospy.Duration(float(tokens[start]))
                    res.traj.points.append(pt)
                return res

        sub = rospy.Subscriber("/joint_states", sensor_msgs.msg.JointState, UpdateRobotJoints,queue_size=1)
        s = rospy.Service('MoveToHandPosition', orrosplanning.srv.MoveToHandPosition, MoveToHandPositionFn)
        print 'openrave planning service started'

        if options.ipython:
            ipshell = IPShellEmbed(argv='',banner = 'Dropping into IPython',exit_msg = 'Leaving Interpreter, back to program.')
            ipshell(local_ns=locals())
        else:
            rospy.spin()
    finally:
        RaveDestroy()