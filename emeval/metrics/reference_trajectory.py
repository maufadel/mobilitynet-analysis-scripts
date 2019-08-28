import scipy.interpolate as sci
import geopandas as gpd
import shapely as shp
import random as random
import math
import arrow
import pandas as pd

R = 40075017 # circumference of the earth in meters
random.seed(1)

####
# BEGIN: Building blocks of the final implementations
####

####
# BEGIN: NORMALIZATION
####

def fill_gt_linestring(e):
    e["ground_truth"]["linestring"] = shp.geometry.LineString(coordinates=
        e["ground_truth"]["leg"]["route_coords"]["geometry"]["coordinates"])

def to_gpdf(location_df):
    return gpd.GeoDataFrame(
        location_df, geometry=location_df.apply(
            lambda lr: shp.geometry.Point(lr.longitude, lr.latitude), axis=1))

def get_int_aligned_trajectory(location_df, tz="UTC"):
    lat_fn = sci.interp1d(x=location_df.ts, y=location_df.latitude)
    lon_fn = sci.interp1d(x=location_df.ts, y=location_df.longitude)
    # In order to avoid extrapolation, we use ceil for the first int and floor
    # for the last int
    first_int_ts = math.ceil(location_df.ts.iloc[0])
    last_int_ts = math.floor(location_df.ts.iloc[-1])
    new_ts_range = [float(ts) for ts in range(first_int_ts, last_int_ts, 1)]
    new_fmt_time_range = [arrow.get(ts).to(tz) for ts in new_ts_range]
    new_lat = lat_fn(new_ts_range)
    new_lng = lon_fn(new_ts_range)
    new_gpdf = gpd.GeoDataFrame({
        "latitude": new_lat,
        "longitude": new_lng,
        "ts": new_ts_range,
        "fmt_time": new_fmt_time_range,
        "geometry": [shp.geometry.Point(x, y) for x, y in zip(new_lng, new_lat)]
    })
    return new_gpdf

####
# END: NORMALIZATION
####

####
# BEGIN: DISTANCE CALCULATION
####

def add_gt_error_projection(location_gpdf, gt_linestring):
    location_gpdf["gt_distance"] = location_gpdf.distance(gt_linestring) * (R/360)
    location_gpdf["gt_projection"] = location_gpdf.geometry.apply(
        lambda p: gt_linestring.project(p) * (R/360))

def add_t_error(location_gpdf_a, location_gpdf_b):
    location_gpdf_a["t_distance"] = location_gpdf_a.distance(location_gpdf_b) * (R/360)
    location_gpdf_b["t_distance"] = location_gpdf_a.t_distance

def add_self_project(location_gpdf_a):
    loc_linestring = shp.geometry.LineString(coordinates=list(zip(
        location_gpdf.longitude, location_gdpf.latitude)))
    location_gpdf["s_projection"] = location_gpdf.geometry.apply(
        lambda p: loc_linestring.project(p) * (R/360))

####
# END: DISTANCE CALCULATION
####

####
# BEGIN: MERGE
####

# Assumes both entries exist
def b_merge_midpoint(loc_row):
    # print("merging %s" % loc_row)
    assert not pd.isnull(loc_row.geometry_i) and not pd.isnull(loc_row.geometry_a)
    midpoint = shp.geometry.LineString(coordinates=[loc_row.geometry_a, loc_row.geometry_i]).interpolate(0.5, normalized=True)
    # print(midpoint)
    final_geom = (midpoint, "midpoint")
    return final_geom

def b_merge_random(loc_row):
    # print("merging %s" % loc_row)
    assert not pd.isnull(loc_row.geometry_i) and not pd.isnull(loc_row.geometry_a)
    r_idx = random.choice(["geometry_a","geometry_i"])
    rp = loc_row[r_idx]
    # print(midpoint)
    final_geom = (rp, r_idx)
    return final_geom

def b_merge_closer_gt_dist(loc_row):
    # print("merging %s" % loc_row)
    assert not pd.isnull(loc_row.geometry_i) and not pd.isnull(loc_row.geometry_a)
    if loc_row.gt_distance_a < loc_row.gt_distance_i:
        final_geom = (loc_row.geometry_a, "android")
    else:
        final_geom = (loc_row.geometry_i, "ios")
    return final_geom

def b_merge_closer_gt_proj(loc_row):
    # print("merging %s" % loc_row)
    assert not pd.isnull(loc_row.geometry_i) and not pd.isnull(loc_row.geometry_a)
    if loc_row.gt_projection_a < loc_row.gt_projection_i:
        final_geom = (loc_row.geometry_a, "android")
    else:
        final_geom = (loc_row.geometry_i, "ios")
    return final_geom

def collapse_inner_join(loc_row, b_merge_fn):
    """
    Collapse a merged row. The merge was through inner join so both sides are
    known to exist
    """
    final_geom, source = b_merge_fn(loc_row)
    return {
        "ts": loc_row.ts,
        "longitude": final_geom.x,
        "latitude": final_geom.y,
        "geometry": final_geom,
        "source": source
    }

def collapse_outer_join_stateless(loc_row, b_merge_fn):
    """
    Collapse a merged row through outer join. This means that we can have
    either the left side or the right side, or both.
    - If only one side exists, we use it.
    - If both sides exist, we merge using `b_merge_fn`
    """
    source = None
    if pd.isnull(loc_row.geometry_i):
        assert not pd.isnull(loc_row.geometry_a)
        final_geom = loc_row.geometry_a
        source = "android"
    elif pd.isnull(loc_row.geometry_a):
        assert not pd.isnull(loc_row.geometry_i)
        final_geom = loc_row.geometry_i
        source = "ios"
    else:
        final_geom, source = b_merge_fn(loc_row)
    return {
        "ts": loc_row.ts,
        "longitude": final_geom.x,
        "latitude": final_geom.y,
        "geometry": final_geom,
        "source": source
    }

def collapse_outer_join_dist_so_far(loc_row, more_details_fn = None):
    """
    Collapse a merged row through outer join. This means that we can have
    either the left side or the right side, or both. In this case, we also
    want to make sure that the trajectory state is "progressing". In this only
    current implementation, we check that the distance along the ground truth
    trajectory is progressively increasing.  Since this can be complex to debug,
    the `more_details` function returns `True` for rows for which we need more
    details of the computation.
    """
    global distance_so_far

    source = None
    more_details = False
    EMPTY_POINT = shp.geometry.Point()

    if more_details_fn is not None and more_details_fn(loc_row):
        more_details = True

    if more_details:
        print(loc_row.gt_projection_a, loc_row.gt_projection_i)
    if pd.isnull(loc_row.geometry_i):
        assert not pd.isnull(loc_row.geometry_a)
        if loc_row.gt_projection_a > distance_so_far:
            final_geom = loc_row.geometry_a
            source = "android"
        else:
            final_geom = EMPTY_POINT
    elif pd.isnull(loc_row.geometry_a):
        assert not pd.isnull(loc_row.geometry_i)
        if loc_row.gt_projection_i > distance_so_far:
            final_geom = loc_row.geometry_i
            source = "ios"
        else:
            final_geom = EMPTY_POINT
    else:
        assert not pd.isnull(loc_row.geometry_i) and not pd.isnull(loc_row.geometry_a)
        choice_series = gpd.GeoSeries([loc_row.geometry_a, loc_row.geometry_i])
        gt_projection_line_series = gpd.GeoSeries([loc_row.gt_projection_a, loc_row.gt_projection_i])
        if more_details:
            print("gt_projection_line = %s" % gt_projection_line_series)
        distance_from_last_series = gt_projection_line_series.apply(lambda d: d - distance_so_far)
        if more_details:
            print("distance_from_last_series = %s" % distance_from_last_series)

        # assert not (distance_from_last_series < 0).all(), "distance_so_far = %s, distance_from_last = %s" % (distance_so_far, distance_from_last_series)
        if (distance_from_last_series < 0).all():
            if more_details:
                print("all distances are negative, skipping...")
            final_geom = EMPTY_POINT
        else:
            if (distance_from_last_series < 0).any():
                # avoid going backwards along the linestring (wonder how this works with San Jose u-turn)
                closer_idx = distance_from_last_series.idxmax()
                if more_details:
                    print("one distance is going backwards, found closer_idx = %d" % closer_idx)

            else:
                distance_from_gt_series = gpd.GeoSeries([loc_row.gt_distance_a, loc_row.gt_distance_i])
                if more_details:
                    print("distance_from_gt_series = %s" % distance_from_gt_series)
                closer_idx = distance_from_gt_series.idxmin()
                if more_details:
                    print("both distances are positive, found closer_idx = %d" % closer_idx)

            if closer_idx == 0:
                source = "android"
            else:
                source = "ios"
            final_geom = choice_series.loc[closer_idx]

    if final_geom != EMPTY_POINT:
        if source == "android":
            distance_so_far = loc_row.gt_projection_a
        else:
            assert source == "ios"
            distance_so_far = loc_row.gt_projection_i
        
    if more_details:
        print("final_geom = %s, new_distance_so_far = %s" % (final_geom, distance_so_far))
    if final_geom == EMPTY_POINT:
        return {
            "ts": loc_row.ts,
            "longitude": pd.np.nan,
            "latitude": pd.np.nan,
            "geometry": EMPTY_POINT,
            "source": source
        }
    else:
        return {
            "ts": loc_row.ts,
            "longitude": final_geom.x,
            "latitude": final_geom.y,
            "geometry": final_geom,
            "source": source
        }

####
# END: MERGE
####

####
# END: Building blocks of the final implementations
####

####
# BEGIN: Combining into actual reference constructions
####

def ref_ct_general(location_df_a, location_df_i, b_merge_fn, dist_threshold,
        tz="UTC"):
    new_location_df_a = get_int_aligned_trajectory(location_df_a, tz)
    new_location_df_i = get_int_aligned_trajectory(location_df_i, tz)
    add_t_error(new_location_df_a, new_location_df_i)
    filtered_location_df_a = new_location_df_a.query("t_distance < @dist_threshold")
    filtered_location_df_i = new_location_df_i.query("t_distance < @dist_threshold")
    merged_df = pd.merge(filtered_location_df_a, filtered_location_df_i, on="ts",
        how="inner", suffixes=("_a", "_i")).sort_values(by="ts", axis="index")
    initial_reference_gpdf = gpd.GeoDataFrame(list(merged_df.apply(b_merge_fn, axis=1)))
    assert len(initial_reference_gpdf.latitude.notnull()) == 0
    return initial_reference_gpdf

####
# END: Combining into actual reference constructions
####
