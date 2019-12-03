#!/usr/bin/env python

import rospy, time, cv2
import numpy as np
from math import sin, cos, atan2, asin, exp, sqrt
from matplotlib import pyplot as plt
from std_msgs.msg import String, Float64, Empty
from geometry_msgs.msg import PoseStamped, Twist, PoseWithCovariance
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image
from cv_bridge import CvBridge, CvBridgeError
from decimal import Decimal

# observation factors for camera (tuning param), should not be necessary if camera is properly calibrated and pnp is working
obs_factor_x = 1.0
obs_factor_y = 1.0
obs_factor_z = 1.0

obs_offset_x = 0.0
obs_offset_y = 0.0
obs_offset_z = 0.0

pose_rel = Odometry()

img = np.zeros((480,640,3), np.uint8)
raw_image = Image()
bin_image = Image()
contour_image = Image()
corner_image = Image()
pose_image = Image()
debug_image = Image()

bridge = CvBridge()

pose_target_in = Odometry()

def thresholding():
	global raw_image, bin_image
	global img, frame, img_orig, blank_image, img_lines_bin, img_corners
	global height, width, scale

	try:
		img = bridge.imgmsg_to_cv2(raw_image, "bgr8")
	except CvBridgeError as e:
		print(e)

	#Resize image
	scale = .4
	width = int(img.shape[1] * scale)
	height = int(img.shape[0] * scale)
	dim = (width, height) #can also just specify desired dimensions
	frame = cv2.resize(img, dim, interpolation = cv2.INTER_AREA)

	img = frame.copy()
	img_orig = frame.copy()
	blank_image = np.zeros(shape=[height, width, 3], dtype=np.uint8)

	#Convert from BGR to HSV colorspace
	frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV);

	#Guassian blur
	blur_params = (5,5)
	frame = cv2.GaussianBlur(frame,blur_params,cv2.BORDER_DEFAULT)

	#yellow
	lower = (0, 70, 40) #lower threshhold values (H, S, V)
	upper = (90, 255, 255) #upper threshhold values (H, S, V)
	frame = cv2.inRange(frame, lower, upper)

	# ##with single G
	# #yellow
	# P_thres = 2

	# frameb = np.zeros(shape=[height,width, 1], dtype=np.uint8)
	# mu = np.array([ 0.0889, 0.4376, 0.7321])
	# sigma = np.array([[0.0010, 0.0012, 0.0047],[0.0012, 0.0040, 0.0060],[0.0047, 0.0060, 0.0252]])
	# sigma_inv = np.linalg.inv(sigma)
	# DEN = ((2*3.14)**(3/2))*sqrt(np.linalg.det(sigma))
	# for i in range(height):
	#     for j in range(width):
	#         x = frame[i][j]/255.0
	#         v = -0.5*np.dot((x-mu),np.matmul(sigma_inv,(x-mu)))
	#         NUM = exp(v)
	#         P = NUM/DEN
	#         if (P>P_thres):
	#             frameb[i][j] = 1
	#         else:
	#             frameb[i][j] = 0
	# frame = frameb

	#Erosion/dilation
	kernel = np.ones((2,2), np.uint8) 
	frame = cv2.erode(frame, kernel, iterations=1)
	kernel = np.ones((4,4), np.uint8) 
	frame = cv2.dilate(frame, kernel, iterations=1) 

	bin_image = bridge.cv2_to_imgmsg(frame, "8UC1")

def get_corners():
	global contour_image, corner_image
	global frame, img_centroids
	global height, width

	#Find contours and save only the biggest one
	_,contours,_ = cv2.findContours(frame, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
	frame = np.zeros(shape=[height,width, 1], dtype=np.uint8) 
	if len(contours) > 0:
	    areas = np.array([cv2.contourArea(cnt) for cnt in contours])
	    idxs  = areas.argsort()
	    cntsSorted = [contours[i] for i in idxs]
	    # if areas[idxs[-1]] > 0.0*height*width: #threshold for min contour area
	    frame = cv2.drawContours(frame, [cntsSorted[-1]], 0, 255, thickness=cv2.FILLED)

	#Erosion/dilation on biggest contour binary
	kernel = np.ones((4,4), np.uint8) 
	frame = cv2.erode(frame, kernel, iterations=1)
	kernel = np.ones((4,4), np.uint8)
	frame = cv2.dilate(frame, kernel, iterations=1)

	#Find edges of contour
	_,contours,_ = cv2.findContours(frame, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
	
	frame = np.zeros(shape=[height,width, 1], dtype=np.uint8)

	# rospy.loginfo('no of contours %f',len(contours))
	corners = []
	if (len(contours)>0):
		# # approximate the contour
		epsilon = 0.04*cv2.arcLength(contours[0],True)
		contours[0] = cv2.approxPolyDP(contours[0],epsilon,True)
		# convex_hull
		contours[0] = cv2.convexHull(contours[0])
		#approximate the contour
		epsilon = 0.04*cv2.arcLength(contours[0],True)
		contours[0] = cv2.approxPolyDP(contours[0],epsilon,True)
		if (len(contours[0])<5):
			# draw the contour
			frame = cv2.drawContours(frame, [contours[0]], 0, 255, thickness=1)

			if (cv2.arcLength(contours[0], True)>120):
				cluster_mean = contours[0]
				for i in range(len(cluster_mean)):
					e=5
					if (cluster_mean[i][0][0]>e and cluster_mean[i][0][0]<width-e and cluster_mean[i][0][1]>e and cluster_mean[i][0][1]<height-e ):
						corners.append([cluster_mean[i][0][1], cluster_mean[i][0][0]])
	
	corners = np.asarray(corners)

	#Overlay final corners on original image
	img_centroids = img_orig.copy()
	for i in range(len(corners)):
		center = (corners[i][1],corners[i][0])
		cv2.circle(img_centroids, center, 2, [0,255,0], 5)

	contour_image = bridge.cv2_to_imgmsg(frame, "8UC1")
	corner_image = bridge.cv2_to_imgmsg(img_centroids, "8UC3")

	return corners

def pose_solve(cluster_mean):
	global pose_rel, pub_pose_rel
	global translation_pnp, rotation_pnp
	global camera_matrix, dist_coeffs, image_points, model_points_yellow
	global scale
	if len(cluster_mean)>3:
		#Order corner points clockwise from top left (origin) 
		#first sort in ascending y values
		idxs  = cluster_mean[:,0].argsort()
		corner_sort_y = [cluster_mean[i,0] for i in idxs]
		corner_sort_x = [cluster_mean[i,1] for i in idxs]
		#now sort x values accordingly
		if corner_sort_x[0]>corner_sort_x[1]:
			hold_x = corner_sort_x[0]
			hold_y = corner_sort_y[0]
			corner_sort_x[0] = corner_sort_x[1]
			corner_sort_x[1] = hold_x
			corner_sort_y[0] = corner_sort_y[1]
			corner_sort_y[1] = hold_y
		if corner_sort_x[3]>corner_sort_x[2]:
			hold_x = corner_sort_x[2]
			hold_y = corner_sort_y[2]
			corner_sort_x[2] = corner_sort_x[3]
			corner_sort_x[3] = hold_x
			corner_sort_y[2] = corner_sort_y[3]
			corner_sort_y[3] = hold_y     
	
		image_points = np.array([   #(x,y)
									(corner_sort_x[0], corner_sort_y[0]),
									(corner_sort_x[1], corner_sort_y[1]),
									(corner_sort_x[2], corner_sort_y[2]),
									(corner_sort_x[3], corner_sort_y[3]),
								], dtype="double")
		# model_points_yellow = np.array([ 
		# 								(-0.5*0.84, -0.5*0.43, 0.0),    #TOP LEFT CORNER IS ORIGIN (x,y,z)
		# 								(0.5*0.84, -0.5*0.43, 0.0),
		# 								(0.81-0.5*84, 0.5*0.43, 0.0),
		# 								(.03-0.5*84, 0.5*.43, 0.0),
		# 								])
		model_points_yellow = np.array([ 
										(0.0, 0.0, 0.0),    #TOP LEFT CORNER IS ORIGIN (x,y,z)
										(.8763, 0.0, 0.0),
										(.8573, 0.4826, 0.0),
										(.0191, 0.4826, 0.0),
										])

	    #model_points_purple = np.array([
	    #                                (0,0,0),
	    #                                (.82,0,0),
	    #                                (.82,0,.43),
	    #                                (0,0,.43),
	    #                        ])

		focal_length_x = 743.595409 #get from camera calibration
		focal_length_y = 750.175831 #get from camera calibration
		size = frame.shape
		center = (size[1]/2, size[0]/2)
		# print(scale)
		# center = (scale*357.244818, scale*192.270976)
		camera_matrix = np.array(
								[[focal_length_x, 0, center[0]],
								[0, focal_length_y, center[1]],
								[0, 0, 1]], dtype = "double"
								)
		dist_coeffs = np.array([-0.337798, 0.142319, 0.001475, 0.003604, 0.0]) #get from camera calibration

		(success, rotation_pnp, translation_pnp) = cv2.solvePnP(model_points_yellow, image_points, camera_matrix, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE)
		#returns a rotation and translation matrix of the extrinsic matrix of the camera 
		#i.e. rotation of the camera relative to fixed world origin (top left corner of window)

		if (abs(rotation_pnp[0])<0.4 and abs(rotation_pnp[2])<0.4):
			
			rotation_pnp_pub = rotation_pnp.copy()
			# if(rotation_pnp_pub[0]>0):
			# 	rotation_pnp_pub[0] = rotation_pnp_pub[0] - 3.14
			# elif(rotation_pnp_pub[0]<0):
			# 	rotation_pnp_pub[0] = rotation_pnp_pub[0] + 3.14

			# rospy.loginfo('roll %f \t pitch %f \t yaw %f', rotation_pnp_pub[0], rotation_pnp_pub[1], rotation_pnp_pub[2])

			rotation_pnp_q = euler_to_quaternion(rotation_pnp_pub[0],rotation_pnp_pub[1],rotation_pnp_pub[2])
			pose_rel.header.frame_id = "odom"
			pose_rel.child_frame_id = "base_link"
			pose_rel.header.stamp = rospy.get_rostime()
			pose_rel.pose.pose.position.x = translation_pnp[0]
			pose_rel.pose.pose.position.y = translation_pnp[1]
			pose_rel.pose.pose.position.z = translation_pnp[2]
			pose_rel.twist.twist.linear.x = 0.0
			pose_rel.twist.twist.linear.y = 0.0
			pose_rel.twist.twist.linear.z = 0.0
			pose_rel.pose.pose.orientation.w = rotation_pnp_q[0]
			pose_rel.pose.pose.orientation.x = rotation_pnp_q[1]
			pose_rel.pose.pose.orientation.y = rotation_pnp_q[2]
			pose_rel.pose.pose.orientation.z = rotation_pnp_q[3]
			pub_pose_rel.publish(pose_rel)

			return True
	else:
		return False

# if window relative position and orientation are in camera/body frame
def pose_cam2in():
	global pose_rel, pose_gate_in, x, y, z, yaw
	global pub_pose_gate_in

	x_obj_rel_c = pose_rel.pose.pose.position.x
	y_obj_rel_c = pose_rel.pose.pose.position.y
	z_obj_rel_c = pose_rel.pose.pose.position.z

	# camera to body frame
	x_obj_rel_b = obs_factor_x * -y_obj_rel_c + obs_offset_x
	y_obj_rel_b = obs_factor_y * -x_obj_rel_c + obs_offset_y
	z_obj_rel_b = obs_factor_z * -z_obj_rel_c + obs_offset_z

	rospy.loginfo('x_b %f \t y_b %f \t z_b %f', x_obj_rel_b, y_obj_rel_b, z_obj_rel_b)

	# body to inertial frame rotation transform
	x_obj_rel_in =  x_obj_rel_b*cos(yaw) - y_obj_rel_b*sin(yaw)
	y_obj_rel_in = x_obj_rel_b*sin(yaw) + y_obj_rel_b*cos(yaw)
	z_obj_rel_in = z_obj_rel_b

	# inertial frame shift transform
	x_obj = x + x_obj_rel_in
	y_obj = y + y_obj_rel_in
	z_obj = z + z_obj_rel_in

	pose_gate_in.header.frame_id = "odom"
	pose_gate_in.child_frame_id = "base_link"
	pose_gate_in.header.stamp = rospy.get_rostime()
	pose_gate_in.pose.pose.position.x = x_obj
	pose_gate_in.pose.pose.position.y = y_obj
	pose_gate_in.pose.pose.position.z = z_obj
	pose_gate_in.twist.twist.linear.x = 0.0
	pose_gate_in.twist.twist.linear.y = 0.0
	pose_gate_in.twist.twist.linear.z = 0.0
	pose_gate_in.pose.pose.orientation.w = 1.0
	pose_gate_in.pose.pose.orientation.x = 0.0
	pose_gate_in.pose.pose.orientation.y = 0.0
	pose_gate_in.pose.pose.orientation.z = 0.0

	pub_pose_gate_in.publish(pose_gate_in)

	# rospy.loginfo('x %f \t y %f \t z %f \t yaw %f', x_obj, y_obj, z_obj, yaw_obj)
	# rospy.loginfo('x %f \t y %f \t z %f \t yaw %f', x_obj_rel_in, y_obj_rel_in, z_obj_rel_in, yaw_obj)

def euler_to_quaternion(roll, pitch, yaw):

        qw = cos(roll/2) * cos(pitch/2) * cos(yaw/2) + sin(roll/2) * sin(pitch/2) * sin(yaw/2)
        qx = sin(roll/2) * cos(pitch/2) * cos(yaw/2) - cos(roll/2) * sin(pitch/2) * sin(yaw/2)
        qy = cos(roll/2) * sin(pitch/2) * cos(yaw/2) + sin(roll/2) * cos(pitch/2) * sin(yaw/2)
        qz = cos(roll/2) * cos(pitch/2) * sin(yaw/2) - sin(roll/2) * sin(pitch/2) * cos(yaw/2)

        return [qw, qx, qy, qz]

def quaternion_to_euler(w, x, y, z):

    t0 = +2.0 * (w * x + y * z)
    t1 = +1.0 - 2.0 * (x * x + y * y)
    roll = atan2(t0, t1)
    t2 = +2.0 * (w * y - z * x)
    t2 = +1.0 if t2 > +1.0 else t2
    t2 = -1.0 if t2 < -1.0 else t2
    pitch = asin(t2)
    t3 = +2.0 * (w * z + x * y)
    t4 = +1.0 - 2.0 * (y * y + z * z)
    yaw = atan2(t3, t4)
    # return [yaw, pitch, roll]
    return [roll, pitch, yaw]

def pose_display(cluster_mean):
	global img_orig, pose_rel, pose_image, img_centroids
	global camera_matrix, dist_coeffs, image_points, model_points_yellow
	global translation_pnp, rotation_pnp
		#re-project line onto each corner to see 3D orientation found by solvePnP
	# img_centroids = img_orig.copy()
	if (len(cluster_mean)>3): 
		# translation_pnp = np.array([pose_rel.pose.pose.position.x,pose_rel.pose.pose.position.y,pose_rel.pose.pose.position.z])
		# rotation_pnp = np.array(quaternion_to_euler(pose_rel.pose.pose.orientation.w, pose_rel.pose.pose.orientation.x, pose_rel.pose.pose.orientation.y, pose_rel.pose.pose.orientation.z))
		#project a line of length l_test on each corner corner NEED TO ORDER THE CLUSTER_MEAN ARRAY
		l_test = .5
		(gate_origin, jacobian) = cv2.projectPoints(np.array([(0.0, 0.0, l_test)]), rotation_pnp, translation_pnp, camera_matrix, dist_coeffs)
		p1 = ( int(image_points[0][0]), int(image_points[0][1]))
		p2 = ( int(gate_origin[0][0][0]), int(gate_origin[0][0][1]))
		img_centroids = cv2.line(img_centroids, p1, p2, (0,255,0), 2)
		
		(gate_origin, jacobian) = cv2.projectPoints(np.array([(model_points_yellow[1][0],model_points_yellow[1][1], l_test)]), rotation_pnp, translation_pnp, camera_matrix, dist_coeffs)
		p1 = ( int(image_points[1][0]), int(image_points[1][1]))
		p2 = ( int(gate_origin[0][0][0]), int(gate_origin[0][0][1]))
		img_centroids = cv2.line(img_centroids, p1, p2, (0,255,0), 2)
		
		(gate_origin, jacobian) = cv2.projectPoints(np.array([(model_points_yellow[2][0],model_points_yellow[2][1], l_test)]), rotation_pnp, translation_pnp, camera_matrix, dist_coeffs)
		p1 = ( int(image_points[2][0]), int(image_points[2][1]))
		p2 = ( int(gate_origin[0][0][0]), int(gate_origin[0][0][1]))
		img_centroids = cv2.line(img_centroids, p1, p2, (0,255,0), 2)
	
		(gate_origin, jacobian) = cv2.projectPoints(np.array([(model_points_yellow[3][0],model_points_yellow[3][1], l_test)]), rotation_pnp, translation_pnp, camera_matrix, dist_coeffs)
		p1 = ( int(image_points[3][0]), int(image_points[3][1]))
		p2 = ( int(gate_origin[0][0][0]), int(gate_origin[0][0][1]))
		img_centroids = cv2.line(img_centroids, p1, p2, (0,255,0), 2)
		
		font                   = cv2.FONT_HERSHEY_SIMPLEX
		# bottomLeftCornerOfText1= ( int(image_points[0][0]), int(image_points[0][1]))
		# bottomLeftCornerOfText2= ( int(image_points[0][0]), int(image_points[0][1])+20)
		bottomLeftCornerOfText1= ( 10, 20)
		bottomLeftCornerOfText2= ( 10, 20+20)
		fontScale              = 0.4
		fontColor              = (0,0,255)
		lineType               = 2

		x = Decimal(translation_pnp[0][0])
		y = Decimal(translation_pnp[1][0])
		z = Decimal(translation_pnp[2][0])
		roll = Decimal(rotation_pnp[0][0])
		pitch = Decimal(rotation_pnp[1][0])
		yaw = Decimal(rotation_pnp[2][0])
		txt1 = 'position: ' + str(round(x, 2)) + ', ' + str(round(y, 2)) + ', ' + str(round(z, 2))
		txt2 = 'orientation: ' + str(round(roll, 2)) + ', ' + str(round(pitch, 2)) + ', ' + str(round(yaw, 2))
		cv2.putText(img_centroids, txt1,
		    bottomLeftCornerOfText1, 
		    font, 
		    fontScale,
		    fontColor,
		    lineType)
		cv2.putText(img_centroids, txt2,
		    bottomLeftCornerOfText2, 
		    font, 
		    fontScale,
		    fontColor,
		    lineType)

	pose_image = bridge.cv2_to_imgmsg(img_centroids, "8UC3")

def callback(image):
	global raw_image
	raw_image = image

pub_pose_gate_in = rospy.Publisher('/pose_gate_in', Odometry, queue_size=10)
def main():
	global raw_image, bin_image, debug_image, pose_rel, pub_pose_rel
	rospy.init_node('window_detect', anonymous=True)

	pub_pose_rel = rospy.Publisher('/pose_rel_win', Odometry, queue_size=10)
	pub_bin_image = rospy.Publisher('/bin_image', Image, queue_size=10)
	pub_contour_image = rospy.Publisher('/contour_image', Image, queue_size=10)
	pub_corner_image = rospy.Publisher('/corner_image', Image, queue_size=10)
	# pub_pose_image = rospy.Publisher('/pose_image', Image, queue_size=10)
	pub_pose_image = rospy.Publisher('/pose_image_txt', Image, queue_size=10)
	pub_debug_image = rospy.Publisher('/debug_image', Image, queue_size=10)

	# rospy.Subscriber('/cv_camera/image_raw', Image, callback)
	rospy.Subscriber('/image_raw', Image, callback)
	# rospy.Subscriber('/image_raw_throttle', Image, callback)

	rate = rospy.Rate(20)
	while not rospy.is_shutdown():

		try:
			thresholding()
			corners = get_corners()
			flag_publish = pose_solve(corners)
			if (flag_publish):
				pose_display(corners)
		except:
			rospy.loginfo('Some error ocurred')

		# thresholding()
		# corners = get_corners()
		# flag_publish = pose_solve(corners)
		# if (flag_publish):
		# 	pose_display(corners)

		pub_bin_image.publish(bin_image)
		pub_contour_image.publish(contour_image)
		pub_corner_image.publish(corner_image)
		pub_pose_image.publish(pose_image)
		pub_debug_image.publish(debug_image)
		rate.sleep()

if __name__ == '__main__':
	try:
		main()
	except rospy.ROSInterruptException:
		pass
