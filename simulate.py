import argparse
import numpy as np
from time import time
from random import random


# 处理命令行
# parser = argparse.ArgumentParser()
# parser.add_argument("-n", dest="n", type=int, help="Number of events")
# parser.add_argument("-g", "--geo", dest="geo", type=str, help="Geometry file")
# parser.add_argument("-o", "--output", dest="opt", type=str, help="Output file")
# args = parser.parse_args()

import h5py as h5

# TODO: 模拟顶点
def generate_events(number_of_events):
    '''
    描述：生成事例
    输入：number_of_events: event数量
    输出：ParticleTruth，PhotonTruth两个结构化数组
          ParticleTruth形状为(number_of_events, 5)，具有字段：
            EventID: 事件编号        '<i4'
            x:       顶点坐标x/mm    '<f8'
            y:       顶点坐标y/mm    '<f8'
            z:       顶点坐标z/mm    '<f8'
            p:       顶点动量/MeV    '<f8'
          Photons形状为(不定, 3)，具有字段:
            EventID:  事件编号                       '<i4'
            PhotonID: 光子编号（每个事件单独编号）   '<i4'
            GenTime:  从顶点产生到光子产生的时间/ns  '<f8'
    算法描述：
    1. 生成顶点坐标(x, y, z)
       方法：生成球坐标，r用一个与r^2成正比的采样函数
                         theta和phi均匀分布
             转为xyz
    2. 生成光子数目与GenTime
       方法：先算出卷积后的lambda(t), 得到其最大值lambda*
             定义截止时间，将截止时间内产生的光子作为总共的光子
             用lambda*的齐次泊松分布模拟截止时间内的光子事件
             筛选事件，有lambda(t)/lambda*的可能性事件留下
    3. 转化为输出格式输出
    '''
    raise NotImplementedError

# TODO: 光学部分
n_water = 1.33
n_LS = 1.48
Ri = 17.71
Ro = 19.5
r_PMT = 0.508

def get_PE_probability(x, y, z, PMT_phi, PMT_theta):
    '''
    描述：计算(x, y, z)处产生的光子到达(phi, theta)处PMT的概率
    输入：x, y, z: 顶点坐标/mm
          phi, theta: PMT坐标
    输出：float64类型的概率
    算法描述：
    1. 计算从(x, y, z)到PMT可能的两条光路：折射/反射+折射
       可能的思路：费马原理？
    2. 计算这两条光路的总长度
    3. 用菲涅尔公式计算假设光子正好沿着这个方向，真的会这样走的概率
    4. 返回3中概率*(求和 PMT的有效截面/光路总长度^2)/4pi立体角
    '''
    '''
    shen
    数据结构：光子信息由一个(7, phi_num, theta_num)的矩阵表示
    前三个维度为空间坐标，中间三个为速度，最后一个为强度（强度0表示全反射或面积0）
    '''

    # 初始化所有模拟光线
    phi_num = 100
    theta_num = 100
    phi = np.linspace(0.01, 2*np.pi, phi_num)
    theta = np.pi * np.sin(np.linspace(0.01, np.pi, theta_num))
    phis, thetas = np.meshgrid(phi, theta)

    xs = np.full((phi_num, theta_num), x)
    ys = np.full((phi_num, theta_num), y)
    zs = np.full((phi_num, theta_num), z)
    

    vxs = np.cos(thetas) * np.cos(phis)
    vys = np.cos(thetas) * np.sin(phis)
    vzs = np.sin(thetas)

    coordinates = np.stack((xs, ys, zs))
    velocities = np.stack((vxs, vys, vzs))

    intensities = np.sin(thetas)

    # 读取PMT坐标信息
    PMT_coordinate_x = np.full((phi_num, theta_num), np.cos(PMT_theta) * np.cos(PMT_phi))
    PMT_coordinate_y = np.full((phi_num, theta_num), np.cos(PMT_theta) * np.sin(PMT_phi))
    PMT_coordinate_z = np.full((phi_num, theta_num), np.sin(PMT_theta))
    PMT_coordinates = np.stack((PMT_coordinate_x, PMT_coordinate_y, PMT_coordinate_z))

    

    # 求解折射点
    ts = (-np.einsum('kij, kij->ij', coordinates, velocities) +\
          np.sqrt(np.einsum('kij, kij->ij', coordinates * velocities, coordinates * velocities) -\
         (np.einsum('kij, kij->ij', velocities, velocities))*(np.einsum('kij, kij->ij', coordinates, coordinates)-Ri**2))) /\
          np.einsum('kij, kij->ij', velocities, velocities)  #到达液闪边界的时间
    edge_points = coordinates + np.einsum('ij, kij->ij', ts, velocities)

    # 计算入射角，出射角
    normal_vectors = edge_points
    incidence_vectors = edge_points - coordinates
    incidence_angles = np.einsum('kij, kij->ij', normal_vectors, incidence_vectors)              /\
                       np.sqrt(np.einsum('kij, kij->ij', normal_vectors, normal_vectors))        /\
                       np.sqrt(np.einsum('kij, kij->ij', incidence_vectors, incidence_vectors))
    emergence_angles = np.arcsin(n_LS/n_water * np.sin(incidence_angles))
    

    # 判断全反射
    max_incidence_angle = np.arcsin(n_water/n_LS)
    can_transmit = (lambda x: x<max_incidence_angle)(incidence_angles) * (lambda x: x>=0)(ts)

    # 计算折射系数
    Rs = np.square(np.sin(emergence_angles - incidence_angles)/np.sin(emergence_angles + incidence_angles))
    Rp = np.square(np.tan(emergence_angles - incidence_angles)/np.tan(emergence_angles + incidence_angles))
    T = 1 - (Rs+Rp)/2
    
    # 计算出射光
    new_intensities = np.einsum('ij, ij, ij->ij', intensities, T, can_transmit)
    new_coordinates = edge_points
    lambdas = np.einsum('kij->ij', (velocities/edge_points)**2) #法向量需要拉伸的倍数，方便构造出局部平面直角坐标
    new_velocities = velocities + np.einsum('ij, ij, kij->kij', np.tan(incidence_angles)/np.tan(emergence_angles)+1, np.sqrt(lambdas), edge_points)

    # 判断出射光线能否射中PMT
    new_ts = np.einsum('kij, kij->ij', PMT_coordinates - new_coordinates, new_velocities) /\
             np.einsum('kij, kij->ij', new_velocities, new_velocities)
    nearest_points = new_coordinates + np.einsum('ij, kij->kij', new_ts, new_velocities)
    distances = np.einsum('kij, kij->ij', nearest_points - PMT_coordinates, nearest_points - PMT_coordinates)
    final_intensity = new_intensities * (lambda x: x<r_PMT**2)(distances)

    # 计算射中期望
    prob = np.einsum('ij->', final_intensity) / np.einsum('ij->', intensities)
    return prob

# benchmark
x = np.random.rand(4000) * 10
y = np.random.rand(4000) * 10
z = np.random.rand(4000) * 10
p = np.random.rand(4000) * np.pi * 2
t = np.random.rand(4000) * np.pi 

ti = time()
for step in range(4000):
    get_PE_probability(x[step],y[step],z[step],p[step],t[step])
to = time()
print(to - ti)

# 读入几何文件
# with h5.File(args.geo, "r") as geo:
#     # 只要求模拟17612个PMT
#     PMT_list = geo['Geometry'][:17612]

# # 输出
# with h5.File(args.opt, "w") as opt:
#     # 生成顶点
#     ParticleTruth, PhotonTruth = generate_events(args.n)
    
#     opt['ParticleTruth'] = ParticleTruth

    
#     print("TODO: Write opt file")
