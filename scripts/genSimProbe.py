'''
genSimProbe: double-search法，模拟光子打到某个PMT上的概率

主要接口：gen_interp
'''
import multiprocessing
import numpy as np
import numexpr as ne
from scipy.interpolate import RectBivariateSpline
from tqdm import tqdm
from .utils import n_water, n_LS, n_glass, Ri, Ro, r_PMT, c

# 设置ne最大线程数，防止在并行时占用太多资源
ne.set_num_threads(2)

# 本文件中所有物理量均为SI单位
eta = n_LS / n_water


def transist_once(coordinates, velocities, intensities, times):
    '''
    接收在液闪球内的一族光线，模拟其下一次到达液闪球表面的光学过程
    coordinates: (3,n)
    velocities: (3,n)，其中每个速度矢量都已经归一化
    intensities: (n,)
    times: (n,)
    '''
    # 求解折射点，ts为到达液闪边界的时间
    cv = np.einsum('kn, kn->n', coordinates, velocities)
    ts = -cv + np.sqrt(cv**2 - (np.einsum('kn, kn->n', coordinates, coordinates)-Ri**2))
    edge_points = coordinates + ts * velocities

    # 计算增加的时间
    new_times = times + (n_LS/c)*ts

    # 计算入射角，出射角
    normal_vectors = -edge_points / Ri
    incidence_vectors = velocities
    vertical_of_incidence = np.maximum(
        np.einsum('kn, kn->n', incidence_vectors, normal_vectors), -1
    )
    incidence_angles = np.arccos(-vertical_of_incidence)

    # 判断全反射
    max_incidence_angle = np.arcsin(n_water/n_LS)
    can_transmit = (incidence_angles < max_incidence_angle)
    all_reflect = 1 - can_transmit

    #计算折射光，反射光矢量与位置
    reflected_velocities = velocities - 2 * vertical_of_incidence * normal_vectors
    reflected_coordinates = edge_points

    delta = ne.evaluate('1 - eta**2 * (1 - vertical_of_incidence**2)')
    new_velocities = ne.evaluate(
        '(eta*incidence_vectors - (eta*vertical_of_incidence + sqrt(abs(delta))) * normal_vectors) * can_transmit'
    ) #取绝对值避免出错
    new_coordinates = edge_points

    # 计算折射系数
    emergence_angles = np.arccos(np.minimum(np.einsum('kn, kn->n', new_velocities, -normal_vectors), 1))
    Rs = ne.evaluate(
        '(sin(emergence_angles - incidence_angles)/sin(emergence_angles + incidence_angles))**2'
    )
    Rp = ne.evaluate(
        '(tan(emergence_angles - incidence_angles)/tan(emergence_angles + incidence_angles))**2'
    )
    R = (Rs+Rp) / 2
    T = 1 - R

    # 计算折射光，反射光强度
    new_intensities = np.einsum('n, n, n->n', intensities, T, can_transmit)
    reflected_intensities = np.einsum('n, n, n->n', intensities, R, can_transmit) + all_reflect

    # 输出所有量，按需拿取
    return new_coordinates, new_velocities, new_intensities, new_times, \
           reflected_coordinates, reflected_velocities, reflected_intensities


def transist_twice(coordinates, velocities, intensities, times):
    '''
    接收在液闪球内的一族光线，模拟其经过一次反射后再次到达液闪球表面的光学过程
    '''
    nt, nc, nv, ni = transist_once(coordinates, velocities, intensities, times)[3:]
    return transist_once(nc, nv, ni, nt)


def distance(coordinates, velocities, PMT_coordinates):
    '''
    接收一族光线，给出其未来所有时间内与给定PMT的最近距离
    注意：光线是有方向的，如果光子将越离越远，那么将返回负数距离
    '''
    new_ts = np.einsum('kn, kn->n', PMT_coordinates - coordinates, velocities)
    nearest_points = coordinates + new_ts * velocities
    distances = np.linalg.norm(nearest_points - PMT_coordinates, axis=0) * np.sign(new_ts)
    return distances


def gen_coordinates(x, y, z):
    '''
    生成(3, 1)型的坐标，调用时广播成(3, n)
    '''
    coordinates = np.array([x, y, z]).reshape(3, 1)
    return coordinates


def gen_velocities(phis, thetas):
    '''
    接收两个表示角度的1D-array，生成这两族角度所生成的网格上的归一化速度向量
    '''
    vxs = (np.sin(thetas) * np.cos(phis.reshape(-1, 1))).reshape(-1)
    vys = (np.sin(thetas) * np.sin(phis.reshape(-1, 1))).reshape(-1)
    vzs = np.tile(np.cos(thetas), phis.shape[0])
    velocities = np.stack((vxs, vys, vzs))
    return velocities


def hit_PMT(coordinates, velocities, intensities, times, PMT_coordinates):
    '''
    接收一族光线，模拟其在PMT附近的行为
    返回能打到PMT上的总光强，以及能达到PMT上光线的时间
    '''
    # 取出所有能到达PMT的光线
    distances = distance(coordinates, velocities, PMT_coordinates)
    hit_PMTs = (distances > 0) * (distances < r_PMT)
    if np.all(hit_PMTs == 0):
        return 0, np.zeros(1)
    allowed_coordinates = coordinates[:, hit_PMTs]
    allowed_velocities = velocities[:, hit_PMTs]
    allowed_intensities = intensities[hit_PMTs]
    allowed_times = times[hit_PMTs]
    allowed_PMT_coordinates = PMT_coordinates[:, :allowed_times.shape[0]]

    # 计算到达时间
    PMT2edge = allowed_coordinates - allowed_PMT_coordinates
    ts = -np.einsum('kn, kn->n', PMT2edge, allowed_velocities) +\
        np.sqrt(
            np.einsum('kn, kn->n', PMT2edge, allowed_velocities)**2 -\
            np.einsum('kn, kn->n', PMT2edge, PMT2edge) +\
            r_PMT**2
        )
    all_times = allowed_times + (n_water/c)*ts
    edge_points = allowed_coordinates + ts * allowed_velocities

    # 计算入射角，出射角
    normal_vectors = (edge_points - allowed_PMT_coordinates) / r_PMT
    incidence_vectors = allowed_velocities
    incidence_angles = np.arccos(
        -np.maximum(np.einsum('kn, kn->n', incidence_vectors, normal_vectors), -1)
    )

    # Bonus: 计算进入PMT的折射系数
    emergence_angles = np.arcsin((n_water/n_glass)*incidence_angles)
    Rs = ne.evaluate(
        '(sin(emergence_angles - incidence_angles)/sin(emergence_angles + incidence_angles))**2'
    )
    Rp = ne.evaluate(
        '(tan(emergence_angles - incidence_angles)/tan(emergence_angles + incidence_angles))**2'
    )
    R = (Rs+Rp) / 2
    T = 1 - R

    all_intensity = np.einsum('n, n->', allowed_intensities, T)

    return all_intensity, all_times


def rotate(x, y, z, PMT_phi, PMT_theta, reflect_num):
    '''
    根据reflect_num转动顶点与PMT，为了后续可能的光线打到坐标连续处
    '''
    if reflect_num == 0:
        if PMT_phi != np.pi or PMT_theta != np.pi/2:
            Rz = np.array([[-np.cos(PMT_phi), -np.sin(PMT_phi), 0],
                           [ np.sin(PMT_phi), -np.cos(PMT_phi), 0],
                           [               0,                0, 1]])
            Ry = np.array([[np.sin(PMT_theta), 0, -np.cos(PMT_theta)],
                           [                0, 1,                  0],
                           [np.cos(PMT_theta), 0,  np.sin(PMT_theta)]])
            nx, ny, nz = Ry @ Rz @ np.array((x, y, z))
            return nx, ny, nz, np.pi, np.pi/2
        else:
            return x, y, z, np.pi, np.pi/2
    elif reflect_num == 1:
        if PMT_phi != 0 or PMT_theta != np.pi/2:
            Rz = np.array([[ np.cos(PMT_phi), np.sin(PMT_phi), 0],
                           [-np.sin(PMT_phi), np.cos(PMT_phi), 0],
                           [               0,               0, 1]])
            Ry = np.array([[ np.sin(PMT_theta), 0, np.cos(PMT_theta)],
                           [                 0, 1,                 0],
                           [-np.cos(PMT_theta), 0, np.sin(PMT_theta)]])
            nx, ny, nz = Ry @ Rz @ np.array((x, y, z))
            return nx, ny, nz, 0, np.pi/2
        else:
            return x, y, z, 0, np.pi/2


# 生成初始化试探光线，后续不再变化
try_num = 20000
try_phis = np.random.rand(try_num) * 2 * np.pi
try_thetas = np.arccos(np.random.rand(try_num)*2 - 1)

vxs = np.sin(try_thetas) * np.cos(try_phis)
vys = np.sin(try_thetas) * np.sin(try_phis)
vzs = np.cos(try_thetas)
try_velocities = np.stack((vxs, vys, vzs))
try_intensities = np.ones(try_num)


def get_prob_time(x, y, z, PMT_phi, PMT_theta, reflect_num, acc):
    '''
    模拟给定顶点发出的光子，能够到达某个PMT的期望与时间分布
    '''
    # 根据反射次数决定液闪内模拟方式
    if reflect_num == 0:
        transist = transist_once
    elif reflect_num == 1:
        transist = transist_twice

    # Step0: 预转动PMT
    x, y, z, PMT_phi, PMT_theta = rotate(x, y, z, PMT_phi, PMT_theta, reflect_num)
    # 读取PMT坐标信息
    PMT_x = Ro * np.sin(PMT_theta) * np.cos(PMT_phi)
    PMT_y = Ro * np.sin(PMT_theta) * np.sin(PMT_phi)
    PMT_z = Ro * np.cos(PMT_theta)

    # Step1: 均匀发出试探光线
    try_coordinates = gen_coordinates(x, y, z)
    try_times = np.zeros(try_num)

    # Step2: 寻找距离PMT中心一定距离的折射光
    try_new_coordinates, try_new_velocities = transist(
        try_coordinates, try_velocities, try_intensities, try_times
    )[:2]
    try_PMT_coordinates = gen_coordinates(PMT_x, PMT_y, PMT_z)
    try_distances = distance(try_new_coordinates, try_new_velocities, try_PMT_coordinates)

    # 自动调节d_max，使得粗调得到一个恰当的范围（20根粗射光线）
    d_min = r_PMT + 0.002
    allow_num = 0
    least_allow_num = 16 if reflect_num else 20
    for d_max in np.linspace(d_min, 5, 100):
        allowed_lights = (try_distances > d_min) * (try_distances < d_max)
        allow_num = np.sum(allowed_lights)
        if allow_num > least_allow_num:
            break
    if allow_num <= least_allow_num:
        return 0, np.zeros(1)

    # print(f'dmax = {d_max}')
    # print(f'allowed = {allow_num}')
    allowed_phis = try_phis[allowed_lights]
    phi_start = allowed_phis.min()
    phi_end = allowed_phis.max()
    allowed_thetas = try_thetas[allowed_lights]
    theta_start = allowed_thetas.min()
    theta_end = allowed_thetas.max()

    Omega = (np.cos(theta_start) - np.cos(theta_end)) * (phi_end - phi_start)
    # print(f'phi in {[phi_start, phi_end]}')
    # print(f'theta in {[theta_start, theta_end]}')
    # print(f'Omega = {Omega}')

    # Step3: 在小区域中选择光线
    dense_phi_num = acc
    dense_theta_num = acc
    dense_phis = np.linspace(phi_start, phi_end, dense_phi_num)
    dense_thetas = np.arccos(np.linspace(np.cos(theta_start), np.cos(theta_end), dense_theta_num))

    dense_coordinates = gen_coordinates(x, y, z)
    dense_velocities = gen_velocities(dense_phis, dense_thetas)
    dense_intensities = np.ones(dense_phi_num*dense_theta_num)
    dense_times = np.zeros(dense_phi_num*dense_theta_num)

    # Step4: 判断哪些光线能够到达PMT
    dense_new_coordinates, dense_new_velocities, dense_new_intensities, dense_new_times = \
        transist(dense_coordinates, dense_velocities, dense_intensities, dense_times)[:4]
    dense_PMT_coordinates = gen_coordinates(PMT_x, PMT_y, PMT_z)
    all_intensity, all_times = hit_PMT(
        dense_new_coordinates,
        dense_new_velocities,
        dense_new_intensities,
        dense_new_times,
        dense_PMT_coordinates
    )
    ratio = all_intensity / (dense_phi_num*dense_theta_num)
    # print(f'light num = {all_times.shape[0]}')
    # print(f'ratio = {ratio}')
    prob = ratio * Omega / (4*np.pi)
    # print(f'prob = {prob}')
    # print(f'transist time = {all_times.mean()}')
    return prob, all_times


def get_PE_probability(x, y, z, PMT_phi, PMT_theta, naive=False):
    '''
    功能：给定顶点坐标与PMT坐标，返回顶点发出光子打到PMT上的期望
    x, y, z单位为m
    角度为弧度制
    '''
    PMT_x = Ro * np.sin(PMT_theta) * np.cos(PMT_phi)
    PMT_y = Ro * np.sin(PMT_theta) * np.sin(PMT_phi)
    PMT_z = Ro * np.cos(PMT_theta)
    d = np.sqrt((x-PMT_x)**2 + (y-PMT_y)**2 + (z-PMT_z)**2)
    if naive:
        return r_PMT**2/(4*d**2)  # 平方反比模式
    else:
        prob1 = get_prob_time(x, y, z, PMT_phi, PMT_theta, 0, 300)[0]
        prob2 = get_prob_time(x, y, z, PMT_phi, PMT_theta, 1, 100)[0]
        # print(prob1)
        # print(prob2)
        return prob1+prob2

def get_random_PE_time(x, y, z, PMT_phi, PMT_theta):
    '''
    功能：给定顶点坐标与PMT坐标，返回一个光子可能到达PMT的时间
    x, y, z单位为m
    角度为弧度制
    '''
    prob1, times1 = get_prob_time(x, y, z, PMT_phi, PMT_theta, 0, 30)
    prob2, times2 = get_prob_time(x, y, z, PMT_phi, PMT_theta, 1, 50)
    # print(prob1/prob2)
    p = np.random.rand()
    if p < prob1/(prob1+prob2):     # 即一次折射无反射
        return np.random.choice(times1)
    else:
        return np.random.choice(times2)

def gen_data(input_data):
    '''
    功能：给定顶点坐标与PMT坐标，返回插值时该点所需要的所有数据
        （一次折射到达的概率， 一次反射+一次折射到达的概率，
        一次折射光子的平均到达时间， 一次反射到达光子的平均到达时间，
        标准差1， 标准差2）
    x, y, z单位为m
    角度为弧度制
    '''
    x, y, z, PMT_phi, PMT_theta = input_data
    prob1, times1 = get_prob_time(x, y, z, PMT_phi, PMT_theta, 0, 300)
    prob2, times2 = get_prob_time(x, y, z, PMT_phi, PMT_theta, 1, 150)
    return prob1, prob2, times1.mean(), times2.mean(), times1.std(), times2.std()

def gen_interp():
    '''
    生成插值函数，使用其中的插值函数来近似get_PE_probability与get_random_PE_time
    生成的插值函数支持1D-array输入
    '''
    PRECISION = 100
    print("正在生成插值函数...")

    # 插值用网格
    ro = np.concatenate(
        (
            np.linspace(0.2, 16.5, PRECISION, endpoint=False),
            np.linspace(16.5, Ri, PRECISION//2) # 在边缘处多取一些
        )
    )
    theta = np.linspace(0, np.pi, PRECISION)
    thetas, ros = np.meshgrid(theta, ro)

    # 测试点: yz平面
    xs = np.zeros(PRECISION**2*3//2)
    ys = (np.sin(thetas) * ros).flatten()
    zs = (np.cos(thetas) * ros).flatten()

    prob_t, prob_r, mean_t, mean_r, std_t, std_r = np.zeros(
        (6, PRECISION*3//2, PRECISION)
    )

    # 多线程
    pool = multiprocessing.Pool(8)

    # 模拟光线
    res = np.array(
        list(
            tqdm(
                pool.imap(
                    gen_data,
                    np.stack(
                        (
                            xs,
                            ys,
                            zs,
                            np.zeros(PRECISION**2*3//2),
                            np.zeros(PRECISION**2*3//2)
                        ),
                        axis=-1
                    )
                ),
                total=PRECISION**2*3//2
            )
        )
    )

    # 储存插值点信息
    prob_t = res[:, 0].reshape(-1, PRECISION)
    prob_r = res[:, 1].reshape(-1, PRECISION)
    mean_t = res[:, 2].reshape(-1, PRECISION)
    mean_r = res[:, 3].reshape(-1, PRECISION)
    std_t = res[:, 4].reshape(-1, PRECISION)
    std_r = res[:, 5].reshape(-1, PRECISION)

    # 插值函数
    get_prob_t = RectBivariateSpline(ro, theta, prob_t, kx=1, ky=1, bbox=[0, Ri, 0, np.pi]).ev
    get_prob_r = RectBivariateSpline(ro, theta, prob_r, kx=1, ky=1, bbox=[0, Ri, 0, np.pi]).ev
    get_mean_t = RectBivariateSpline(ro, theta, mean_t, kx=1, ky=1, bbox=[0, Ri, 0, np.pi]).ev
    get_mean_r = RectBivariateSpline(ro, theta, mean_r, kx=1, ky=1, bbox=[0, Ri, 0, np.pi]).ev
    get_std_t = RectBivariateSpline(ro, theta, std_t, kx=1, ky=1, bbox=[0, Ri, 0, np.pi]).ev
    get_std_r = RectBivariateSpline(ro, theta, std_r, kx=1, ky=1, bbox=[0, Ri, 0, np.pi]).ev

    return get_prob_t, get_prob_r, get_mean_t, get_mean_r, get_std_t, get_std_r


# ti = time()
# pool = multiprocessing.Pool(processes=7)

# for step in range(4000):
#     s = pool.apply_async(get_PE_probability, (x[step], y[step], z[step], 0, 0))

# pool.close()
# pool.join()
# print(get_random_PE_time(3,6,-10,0,0))
# to = time()
# print(f'time = {to-ti}')
# x = np.random.random(2000) * 10
# y = np.random.random(2000) * 10
# z = np.random.random(2000) * 10

# if __name__ == '__main__':
#     print(Timer('get_PE_probability(3,6,10,0,0)', setup='from __main__ import get_PE_probability').timeit(4000))
    # for i in range(2000):
    #    get_PE_probability(x[i], y[i], z[i],0,0)
    # print(get_PE_probability(3, 6, 10,0,0))
    # get_PE_probability(np.random.rand()*10, np.random.rand()*10, np.random.rand()*10,0,0)
    # for i in range(4000):
    #     try_phis = np.random.rand(try_num) * 2 * np.pi
    #     try_thetas = np.arccos(np.random.rand(try_num)*2 - 1)

    #     vxs = np.sin(try_thetas) * np.cos(try_phis)
    #     vys = np.sin(try_thetas) * np.sin(try_phis)
    #     vzs = np.cos(try_thetas)
    #     try_velocities = np.stack((vxs, vys, vzs))
    #     try_intensities = np.ones(try_num)
    #     res = get_PE_probability(3, 6, 10,0,0)
    #     print(res)
    #     if np.abs(res*1000000-494)> 10:
    #         print("error", res)
    #         break
